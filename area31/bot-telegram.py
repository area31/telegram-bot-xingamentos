import configparser
import os
import time
import random
import sqlite3
import requests
import html
import sys
import re
import logging
import logging.handlers
from urllib.parse import quote_plus
from typing import Tuple, Optional

# Adiciona o caminho pras bibliotecas locais
sys.path.append('/home/morfetico/.local/lib/python3.12/site-packages/')
import telebot
import shutil
import openai
from openai import OpenAIError
import telegram_format as tf

# Variáveis globais pra controlar o rate limit
start_time = time.time()
request_count = 0

# Configuração do logging
temp_logger = logging.getLogger('temp_init')
temp_handler = logging.FileHandler('bot-telegram.log', mode='a')
temp_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
temp_logger.addHandler(temp_handler)
temp_logger.setLevel(logging.DEBUG)
temp_logger.debug("Iniciando configuração do logging")

# Filtro pra esconder tokens sensíveis no log
class TokenObfuscationFilter(logging.Filter):
    def __init__(self):
        super().__init__()
        self.token_pattern = r'\b\d{8,10}:[A-Za-z0-9_-]{35}\b'

    def filter(self, record):
        try:
            msg = record.getMessage()
            record.msg = re.sub(self.token_pattern, '****:****', msg)
            if hasattr(record, 'args') and record.args:
                record.args = tuple(
                    re.sub(self.token_pattern, '****:****', str(arg))
                    if isinstance(arg, str) else arg
                    for arg in record.args
                )
            return True
        except Exception as e:
            temp_logger.error(f"Erro no TokenObfuscationFilter: {str(e)}")
            return True

# Configuração do log principal
try:
    log_file = 'bot-telegram.log'
    if not os.path.exists(log_file):
        with open(log_file, 'a'):
            os.chmod(log_file, 0o666)
    else:
        os.chmod(log_file, 0o666)

    config_bot = configparser.ConfigParser(comment_prefixes=('#', ';'))
    config_bot.optionxform = str
    if not config_bot.read('bot-telegram.cfg'):
        temp_logger.error("Erro: Não conseguiu ler o bot-telegram.cfg! Usando LOG_LEVEL=INFO.")
        LOG_LEVEL = 'INFO'
    else:
        LOG_LEVEL = config_bot['DEFAULT'].get('LOG_LEVEL', 'INFO').upper()
        temp_logger.debug(f"Configuração lida: LOG_LEVEL={LOG_LEVEL}, BOT_AI={config_bot['DEFAULT'].get('BOT_AI', 'xai')}, XAI_MODEL={config_bot['DEFAULT'].get('XAI_MODEL', 'grok-3-mini-fast-beta')}")

    valid_log_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
    if LOG_LEVEL not in valid_log_levels:
        temp_logger.error(f"LOG_LEVEL inválido: {LOG_LEVEL}. Usando INFO.")
        LOG_LEVEL = 'INFO'

    logger = logging.getLogger('telegram_bot')
    logger.setLevel(getattr(logging, LOG_LEVEL))
    logging.getLogger('').handlers = []

    handler = logging.FileHandler(log_file, mode='a')
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    handler.addFilter(TokenObfuscationFilter())
    handler.setLevel(getattr(logging, LOG_LEVEL))
    logger.addHandler(handler)

    temp_logger.removeHandler(temp_handler)
    temp_handler.close()

    logger.info("Bot iniciado - pronto pra zuar e logar tudo direitinho!")
except Exception as e:
    temp_logger.error(f"Erro na configuração do logging: {str(e)}")
    raise

logging = logger

# Configuração do rate limit
REQUEST_LIMIT = 40
TIME_WINDOW = 60
request_count = 0
start_time = time.time()

# Configuração do Telegram
config_telegram = configparser.ConfigParser()
config_telegram.read('token-telegram.cfg')
TOKEN = config_telegram['DEFAULT']['TOKEN']
bot = telebot.TeleBot(TOKEN)
logging.debug("Token do Telegram lido com sucesso")

# Configuração da CoinCap API
config_coincap = configparser.ConfigParser()
config_coincap.read('token-coincap.cfg')
COINCAP_API_KEY = config_coincap['DEFAULT']['TOKEN']
logging.debug("Token da CoinCap API lido com sucesso")

# Limite de caracteres do Telegram
TELEGRAM_MAX_CHARS = 4096

# Armazenamento em memória
stored_info = {}
chat_memory = {}
last_image_prompt = {}

# Configuração da OpenAI
config_openai = configparser.ConfigParser()
config_openai.read('token-openai.cfg')
OPENAI_API_KEY = config_openai['DEFAULT']['API_KEY']
logging.debug("Token da OpenAI lido com sucesso")

# Configuração da xAI
config_xai = configparser.ConfigParser()
config_xai.read('token-xai.cfg')
XAI_API_KEY = config_xai['DEFAULT']['API_KEY']
logging.debug("Token da xAI lido com sucesso")

# Configuração do bot
BOT_AI = config_bot['DEFAULT'].get('BOT_AI', 'openai')
XAI_MODEL = config_bot['DEFAULT'].get('XAI_MODEL', 'grok-2-latest')
logging.debug(f"Configuração do bot: BOT_AI={BOT_AI}, XAI_MODEL={XAI_MODEL}")

# Parâmetros gerais pra IA
MAX_TOKENS = 1000
TEMPERATURE = 0.8
OPENAI_MODEL = "gpt-4"

# Funções auxiliares
def create_table():
    with sqlite3.connect('frases.db') as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS frases (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            frase TEXT NOT NULL
        );
        """)
    logging.debug("Tabela de frases criada ou verificada")

def insert_frase(frase: str):
    with sqlite3.connect('frases.db') as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO frases (frase) VALUES (?)", (frase,))
        conn.commit()
    logging.debug(f"Frase inserida: {frase}")

def get_random_frase() -> str:
    with sqlite3.connect('frases.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT frase FROM frases ORDER BY RANDOM() LIMIT 1")
        result = cursor.fetchone()
        return result[0] if result else None
    logging.debug("Frase aleatória buscada")

def update_chat_memory(message):
    chat_id = message.chat.id
    if chat_id not in chat_memory:
        chat_memory[chat_id] = []

    role = "user" if message.from_user.id != bot.get_me().id else "assistant"
    # Inclui texto, legenda de foto, ou "[Imagem]" como fallback
    content = message.text or message.caption or "[Imagem]"
    chat_memory[chat_id].append({"role": role, "content": content})
    logging.debug(f"Adicionada ao chat_memory (chat_id {chat_id}, role: {role}): {content[:50]}...")

    # Limite de 20 mensagens
    if len(chat_memory[chat_id]) > 20:
        chat_memory[chat_id] = chat_memory[chat_id][-20:]
        logging.debug(f"Chat_memory para chat_id {chat_id} limitado a 20 mensagens")

    logging.debug(f"Memória de chat atualizada para chat_id {chat_id}: {len(chat_memory[chat_id])} mensagens")

def get_chat_history(message, reply_limit: int = 12) -> list:
    chat_id = message.chat.id
    history = []

    logging.debug(f"Construindo histórico para chat_id {chat_id} com reply_limit {reply_limit}")

    # Coleta mensagens encadeadas (reply_to_message)
    if message.reply_to_message:
        current_message = message
        content = current_message.text or current_message.caption or "[Imagem]"
        history.append({"role": "user", "content": content})
        logging.debug(f"Adicionada mensagem inicial do usuário: {content[:50]}...")
        while current_message.reply_to_message and len(history) < reply_limit:
            previous_message = current_message.reply_to_message
            role = "assistant" if previous_message.from_user.id == bot.get_me().id else "user"
            content = previous_message.text or previous_message.caption or "[Imagem]"
            history.append({"role": role, "content": content})
            logging.debug(f"Adicionada mensagem encadeada (role: {role}): {content[:50]}...")
            current_message = previous_message
        history.reverse()

    # Adiciona mensagens do chat_memory, se disponíveis
    if chat_id in chat_memory and len(history) < reply_limit:
        memory_messages = chat_memory[chat_id].copy()
        # Filtra mensagens já incluídas no encadeamento pra evitar duplicatas
        existing_contents = {msg["content"] for msg in history}
        additional_messages = [
            msg for msg in memory_messages
            if msg["content"] not in existing_contents
        ]
        # Adiciona mensagens do chat_memory até atingir reply_limit
        for msg in additional_messages[:reply_limit - len(history)]:
            history.insert(0, msg)
            logging.debug(f"Adicionada mensagem do chat_memory (role: {msg['role']}): {msg['content'][:50]}...")

    # Limita por tokens
    token_count = count_tokens(history)
    max_allowed_tokens = MAX_TOKENS * 0.9  # 90% de MAX_TOKENS (900 tokens)
    logging.debug(f"Contagem inicial de tokens: {token_count}, máximo permitido: {max_allowed_tokens}")
    while token_count > max_allowed_tokens and history:
        removed_message = history.pop(0)
        token_count = count_tokens(history)
        logging.debug(f"Removida mensagem mais antiga ({removed_message['content'][:50]}...), nova contagem: {token_count}")

    logging.debug(f"Histórico final obtido para chat_id {chat_id}: {len(history)} mensagens")
    return history

def clear_stored_info(user_id):
    if user_id in stored_info:
        del stored_info[user_id]
        logging.info(f"Informações limpas para user_id {user_id}")

def count_tokens(messages):
    total_tokens = 0
    for msg in messages:
        content = msg["content"] or "[Imagem]"
        total_tokens += len(content.split()) + 10
    logging.debug(f"Contagem de tokens: {total_tokens}")
    return total_tokens

def get_prompt() -> str:
    try:
        with open('prompt.cfg', 'r', encoding='utf-8') as arquivo:
            base_prompt = arquivo.read().strip()
            logging.debug("Prompt lido do prompt.cfg")
    except FileNotFoundError:
        base_prompt = (r"Você é um assistente útil e respeitoso em um bot do Telegram. "
                       r"Responda de forma clara, amigável e profissional, mantendo o contexto da conversa. "
                       r"Evite respostas ofensivas ou inadequadas. "
                       r"Se a mensagem for curta (menos de 15 caracteres) ou vaga, peça mais detalhes com base no histórico da conversa. "
                       r"Quando o usuário pedir para 'armazenar a info', guarde a informação em uma lista associada ao ID do usuário. "
                       r"Quando perguntado 'quais são as infos que te pedi pra armazenar?', responda com a lista de informações armazenadas. "
                       r"Se o usuário pedir LaTeX, use o formato de blocos matemáticos do Telegram ($$   ...   $$ para display, $$  ...  $$ para inline). "
                       r"Se pedir 'code LaTeX do Telegram', retorne o código LaTeX puro dentro de um bloco de código ```latex ... ```. "
                       r"Se a pergunta for sobre uma imagem gerada (ex.: 'quem são esses?'), explique que você não vê a imagem, mas pode descrever o que tentou gerar com base no prompt de texto fornecido.")
        logging.warning("prompt.cfg não encontrado, usando prompt padrão")
    return f"{base_prompt}\n\nSua resposta deve ter no máximo 4000 caracteres para caber no limite do Telegram. Evite usar asteriscos (*) ou outros caracteres especiais em excesso, a menos que necessários para formatação."

def formatar_resposta_ia(texto):
    # Log do texto bruto recebido
    logging.debug(f"Texto bruto recebido para formatação: {texto}")

    # Preserva blocos LaTeX
    latex_blocks = []
    def store_latex(match):
        latex_blocks.append(match.group(0))
        return f"__LATEX_{len(latex_blocks)-1}__"

    # Substitui blocos LaTeX separadamente (display e inline)
    texto = re.sub(r'\$\$[\s\S]*?\$\$', store_latex, texto, flags=re.DOTALL)
    texto = re.sub(r'\\$$ [\s\S]*?\\ $$', store_latex, texto, flags=re.DOTALL)

    # Log do texto com placeholders
    logging.debug(f"Texto com placeholders LaTeX: {texto}")

    # Preserva marcadores MarkdownV2 (negrito, itálico, etc.)
    markdown_blocks = []
    def store_markdown(match):
        markdown_blocks.append(match.group(0))
        return f"__MARKDOWN_{len(markdown_blocks)-1}__"

    # Substitui marcadores MarkdownV2
    markdown_patterns = [
        r'\*\*[^\*]+\*\*',  # Negrito
        r'\*[^\*]+\*',      # Itálico
        r'~[^~]+~',         # Riscado
        r'__[^_]+__',       # Sublinhado
        r'\|\|[^\|]+\|\|',  # Spoiler
        r'`[^`]+`',         # Código inline
        r'```[\s\S]*?```'   # Bloco de código
    ]
    for pattern in markdown_patterns:
        texto = re.sub(pattern, store_markdown, texto, flags=re.DOTALL)

    # Log do texto com placeholders Markdown
    logging.debug(f"Texto com placeholders Markdown: {texto}")

    # Aplica escape apenas ao texto fora dos placeholders
    if "Complexidade:" in texto:
        linhas = texto.split("\n")
        elementos = []
        for linha in linhas:
            if linha.strip().startswith(("1.", "2.", "3.", "4.")):
                partes = linha.split(":", 1)
                if len(partes) == 2:
                    titulo, descricao = partes
                    titulo = titulo.strip().replace("**", "").replace("*", "").strip("1.234. ")
                    complexidade = re.search(r'O$$ [^ $$]+\)', descricao)
                    if complexidade:
                        complexidade = complexidade.group(0)
                        descricao = descricao.replace(complexidade, tf.code_md(complexidade))
                    elementos.append(f"- {tf.bold_md(titulo)}: {tf.escape_markdown_v2(descricao.strip())}")
            else:
                elementos.append(tf.escape_markdown_v2(linha.strip()))
        texto = "\n".join(elementos)
    else:
        # Escapa apenas o texto que não é placeholder
        texto = tf.escape_markdown_v2(texto)

    # Log do texto após formatação MarkdownV2
    logging.debug(f"Texto após formatação MarkdownV2: {texto}")

    # Restaura marcadores MarkdownV2
    for i, block in enumerate(markdown_blocks):
        texto = texto.replace(f"__MARKDOWN_{i}__", block)

    # Restaura blocos LaTeX
    for i, block in enumerate(latex_blocks):
        texto = texto.replace(f"__LATEX_{i}__", block)

    # Log do texto final com Markdown e LaTeX restaurados
    logging.debug(f"Texto final com Markdown e LaTeX restaurado: {texto}")

    return texto

def format_price(price: float) -> str:
    return f"{price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

create_table()

# Handlers
@bot.message_handler(func=lambda message: message.text is not None and
                    message.from_user.id != bot.get_me().id and
                    (bot.get_me().username in message.text or
                     (message.reply_to_message is not None and
                      message.reply_to_message.from_user.id == bot.get_me().id)))
def responder(message):
    global start_time, request_count
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text or '[No text]'}")
    logging.debug(f"Pergunta completa de @{username}: {message.text or '[No text]'}")

    # Bloqueia chats privados e bots
    if message.chat.type == 'private' or message.from_user.is_bot:
        response_text = tf.escape_markdown_v2("Desculpe, só respondo em grupos e não a bots!")
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
        return

    # Verifica se tem texto
    if not message.text:
        response_text = tf.escape_markdown_v2("Desculpe, não posso processar mensagens sem texto! Tente enviar uma mensagem com texto.")
        tf.send_markdown(bot, message.chat.id, response_text, reply_to_message_id=message.message_id)
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
        return

    # Ignora mensagens curtas ou irrelevantes
    text_lower = message.text.lower().strip()
    if len(text_lower) < 5 or text_lower in ["ok", "sim", "não", "ta", "blz", "valeu"]:
        response_text = tf.escape_markdown_v2("Beleza, mas me dá mais contexto ou pergunta algo mais específico!")
        tf.send_markdown(bot, message.chat.id, response_text, reply_to_message_id=message.message_id)
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
        return

    # Rate limit
    current_time = time.time()
    if current_time - start_time > TIME_WINDOW:
        start_time = current_time
        request_count = 0
    if request_count >= REQUEST_LIMIT:
        response_text = tf.escape_markdown_v2("Tô de boa, mas muito requisitado agora! Tenta de novo em uns segundos.")
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
        return
    request_count += 1

    # Verifica mensagem curta após imagem
    chat_id = message.chat.id
    if len(text_lower) < 15 and chat_id in last_image_prompt:
        response_text = tf.escape_markdown_v2(
            f"Sua mensagem tá meio pequena! Você tá falando da imagem gerada com o prompt '{last_image_prompt[chat_id]}'? "
            "Eu não vejo a imagem, mas posso descrever algo sobre esse tema ou responder algo mais específico se tu explicar melhor!"
        )
        tf.send_markdown(bot, message.chat.id, response_text, reply_to_message_id=message.message_id)
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
        return

    update_chat_memory(message)
    chat_history = get_chat_history(message, reply_limit=6)
    system_prompt = get_prompt()
    user_id = message.from_user.id

    # Trata pedidos de barras invertidas
    if "manda" in text_lower and (r"\\" in message.text or "backslash" in text_lower):
        response_text = (
            r"Beleza, entendi! Vou usar `\` pra barras invertidas fora de blocos LaTeX, e `\` dentro de expressões matemáticas "
            r"como $$  \sqrt{-1}  $$. Se precisar de algo específico, explica mais!"
        )
        tf.send_markdown(bot, message.chat.id, response_text, reply_to_message_id=message.message_id)
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
        return

    # Trata pedidos de "code LaTeX do Telegram"
    if "code latex" in text_lower and "telegram" in text_lower:
        response_text = (
            "```latex\n"
            "% Fórmula de Euler\n"
            "\\documentclass{article}\n"
            "\\usepackage{amsmath}\n\n"
            "\\begin{document}\n\n"
            "A Fórmula de Euler é $$ e^{i\\pi} + 1 = 0 $$.\n\n"
            "Em geral: $$ e^{i\\theta} = \\cos(\\theta) + i \\sin(\\theta) $$\n\n"
            "\\end{document}\n"
            "```"
        )
        tf.send_markdown(bot, message.chat.id, response_text, reply_to_message_id=message.message_id)
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
        return

    # Trata perguntas sobre imagens
    if any(keyword in text_lower for keyword in ["quem são", "quem é", "o que é isso", "o que são"]) and chat_id in last_image_prompt:
        response_text = tf.escape_markdown_v2(
            f"Eu não vejo a imagem gerada, mas ela foi criada com base no prompt: '{last_image_prompt[chat_id]}'. "
            "Quer que eu descreva algo específico sobre esse tema ou gere outra imagem?"
        )
        tf.send_markdown(bot, message.chat.id, response_text, reply_to_message_id=message.message_id)
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
        return

    # Armazena infos
    if "armazene" in text_lower and ("info" in text_lower or "armazene" in text_lower.split()):
        try:
            info = message.text.split("armazene", 1)[1].replace("a info:", "").strip()
            if not info:
                response_text = tf.escape_markdown_v2("Opa, amigo! Armazenar o quê? Me dá algo pra guardar!")
                tf.send_markdown(bot, message.chat.id, response_text, reply_to_message_id=message.message_id)
                logging.info(f"Resposta enviada para @{username}: {response_text}")
                logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
                return
            if user_id not in stored_info:
                stored_info[user_id] = []
            stored_info[user_id].append(info)
            response_text = tf.escape_markdown_v2("Beleza, guardei a info pra você!")
            tf.send_markdown(bot, message.chat.id, response_text, reply_to_message_id=message.message_id)
            logging.info(f"Resposta enviada para @{username}: {response_text}")
            logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
            return
        except IndexError:
            response_text = tf.escape_markdown_v2("Opa, amigo! Armazenar o quê? Me dá algo pra guardar!")
            tf.send_markdown(bot, message.chat.id, response_text, reply_to_message_id=message.message_id)
            logging.info(f"Resposta enviada para @{username}: {response_text}")
            logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
            return

    # Lista infos armazenadas
    if "quais são as infos que te pedi pra armazenar?" in text_lower or \
       "me diga o que pedi pra armazenar" in text_lower:
        if user_id in stored_info and stored_info[user_id]:
            itens = [f"- {tf.escape_markdown_v2(info)}" for info in stored_info[user_id]]
            resposta = "\n".join([tf.bold_md("Informações armazenadas:")] + itens)
            tf.send_markdown(bot, message.chat.id, resposta, reply_to_message_id=message.message_id)
            logging.info(f"Resposta enviada para @{username}: {resposta}")
            logging.debug(f"Resposta completa enviada para @{username}: {resposta}")
        else:
            response_text = tf.escape_markdown_v2("Você ainda não me passou nenhuma info pra guardar, amigo!")
            tf.send_markdown(bot, message.chat.id, response_text, reply_to_message_id=message.message_id)
            logging.info(f"Resposta enviada para @{username}: {response_text}")
            logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
        return

    # Limpa infos
    if "limpe tudo que armazenou" in text_lower:
        clear_stored_info(user_id)
        response_text = tf.escape_markdown_v2("Feito, amigo! Tudo limpo, não guardei mais nada.")
        tf.send_markdown(bot, message.chat.id, response_text, reply_to_message_id=message.message_id)
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
        return

    # Adiciona infos ao prompt
    if user_id in stored_info:
        system_prompt += f"\nInformações que esse usuário me pediu pra guardar: {', '.join(stored_info[user_id])}"
    system_prompt += (
        r"\nQuando mencionar barras invertidas, use `\` para texto literal fora de LaTeX, "
        r"e `\` dentro de blocos matemáticos como $$  \sqrt{-1}  $$"
    )

    messages = [{"role": "system", "content": system_prompt}] + chat_history

    try:
        start_time = time.time()
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if BOT_AI.lower() == "openai":
                    openai.api_key = OPENAI_API_KEY
                    response = openai.ChatCompletion.create(
                        model=OPENAI_MODEL,
                        messages=messages,
                        max_tokens=MAX_TOKENS,
                        temperature=TEMPERATURE,
                        top_p=0.9,
                        frequency_penalty=0.0,
                        presence_penalty=0.2
                    )
                    answer = response.choices[0].message['content'].strip()
                    logging.debug(f"Resposta bruta da OpenAI para @{username}: {answer}")
                elif BOT_AI.lower() == "xai":
                    headers = {
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {XAI_API_KEY}"
                    }
                    payload = {
                        "messages": messages,
                        "model": XAI_MODEL,
                        "stream": False,
                        "temperature": TEMPERATURE,
                        "max_tokens": MAX_TOKENS
                    }
                    response = requests.post("https://api.x.ai/v1/chat/completions", headers=headers, json=payload)
                    response.raise_for_status()
                    answer = response.json()["choices"][0]["message"]["content"].strip()
                    logging.debug(f"Resposta bruta da xAI para @{username}: {answer}")
                else:
                    raise ValueError(f"Configuração inválida para BOT_AI: {BOT_AI}. Use 'openai' ou 'xai'.")
                break
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    logging.warning(f"Tentativa {attempt + 1} falhou com erro para @{username}: {str(e)}. Tentando novamente...")
                    time.sleep(2 ** attempt)
                    continue
                raise

        response_time = time.time() - start_time
        if not answer or len(answer) < 10:
            answer = "Opa, não consegui processar direito sua instrução. Tenta explicar de novo ou pedir algo diferente!"
            logging.debug(f"Resposta padrão usada para @{username}: {answer}")

        # Formata a resposta
        mensagem = formatar_resposta_ia(answer)
        chat_memory[chat_id].append({"role": "assistant", "content": answer})

        try:
            if message.reply_to_message:
                tf.send_markdown(bot, message.chat.id, mensagem, reply_to_message_id=message.message_id)
            else:
                tf.send_markdown(bot, message.chat.id, mensagem)
            logging.info(f"Resposta enviada para @{username}: {mensagem}")
            logging.debug(f"Resposta completa enviada para @{username} (MarkdownV2): {mensagem}")
        except Exception as e:
            logging.error(f"[ERROR] Falha ao enviar com MarkdownV2 para @{username}: {str(e)}")
            logging.debug(f"Mensagem problemática para @{username}: {mensagem}")
            try:
                if message.reply_to_message:
                    tf.send_html(bot, message.chat.id, tf.escape_html(answer), reply_to_message_id=message.message_id)
                else:
                    tf.send_html(bot, message.chat.id, tf.escape_html(answer))
                logging.info(f"Resposta fallback enviada para @{username}: {answer}")
                logging.debug(f"Resposta completa fallback enviada para @{username} (HTML): {answer}")
            except Exception as e2:
                logging.error(f"[ERROR] Falha no fallback para @{username}: {str(e2)}")
                response_text = tf.escape_markdown_v2("Deu uma zica aqui, brother! Tenta depois!")
                tf.send_markdown(bot, message.chat.id, response_text)
                logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
                logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")

    except OpenAIError as e:
        error_msg = f"[ERROR] Erro na API da OpenAI para @{username}: {str(e)}"
        logging.error(error_msg)
        response_text = tf.escape_markdown_v2("Ops, minha cabeça de IA deu tilt! Tenta de novo!")
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")
    except requests.exceptions.RequestException as e:
        error_msg = f"[ERROR] Erro na API da xAI para @{username}: {str(e)}"
        logging.error(error_msg)
        response_text = tf.escape_markdown_v2("Ops, deu problema com a xAI! Tenta de novo!")
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")
    except Exception as e:
        error_msg = f"[ERROR] Inesperado para @{username}: {str(e)}"
        logging.error(error_msg)
        response_text = tf.escape_markdown_v2("Deu uma zica aqui, brother! Tenta depois!")
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")

@bot.message_handler(commands=['youtube'])
def youtube_search_command(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")

    query = message.text.replace("/youtube", "", 1).strip()
    if not query:
        response_text = tf.escape_html("Por favor, execute o /youtube com algum termo de busca")
        tf.send_html(bot, message.chat.id, response_text)
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
        return

    API_KEY = open("token-google.cfg").read().strip()
    search_url = (
        "https://www.googleapis.com/youtube/v3/search"
        f"?part=snippet&q={requests.utils.quote(query)}"
        "&type=video&maxResults=5"
        f"&key={API_KEY}"
    )

    try:
        resp = requests.get(search_url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])

        if not items:
            response_text = tf.escape_html("Não foram encontrados resultados para a sua pesquisa.")
            tf.send_html(bot, message.chat.id, response_text)
            logging.info(f"Resposta enviada para @{username}: {response_text}")
            logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
            return

        lines = []
        for idx, item in enumerate(items, start=1):
            title = item["snippet"].get("title", "").strip()
            vid = item["id"].get("videoId", "").strip()
            url = f"https://www.youtube.com/watch?v={vid}"
            lines.append(f"{idx}. {tf.escape_html(title)} — {tf.link('Link', url)}")

        header = f"🔎 {tf.bold(tf.escape_html(f'Resultados do YouTube para “{query}”:'))}\n"
        full_text = header + "\n".join(lines)
        tf.send_html(bot, message.chat.id, full_text)
        logging.info(f"Resposta enviada para @{username}: {full_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {full_text}")
    except Exception as e:
        logging.error(f"[ERROR] Handler /youtube para @{username}: {e}", exc_info=True)
        response_text = tf.escape_html("Ops, algo deu errado no /youtube. Tente novamente mais tarde.")
        tf.send_html(bot, message.chat.id, response_text)
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")

@bot.message_handler(commands=['search'])
def search_command(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")

    query = message.text.replace("/search", "", 1).strip()
    if not query:
        response_text = tf.escape_html("Por favor, execute o /search com algum termo de busca")
        tf.send_html(bot, message.chat.id, response_text)
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
        return

    API_KEY = open("token-google.cfg").read().strip()
    SEARCH_ENGINE_ID = open("token-google-engine.cfg").read().strip()
    url = (
        "https://www.googleapis.com/customsearch/v1"
        f"?key={API_KEY}&cx={SEARCH_ENGINE_ID}&q={quote_plus(query)}"
    )

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("items", [])

        if not results:
            response_text = tf.escape_html("Não foram encontrados resultados para a sua pesquisa.")
            tf.send_html(bot, message.chat.id, response_text)
            logging.info(f"Resposta enviada para @{username}: {response_text}")
            logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
            return

        lines = []
        for idx, item in enumerate(results[:5], start=1):
            title = item.get("title", "").strip()
            link = item.get("link", "").strip()
            lines.append(f"{idx}. {tf.escape_html(title)} — {tf.link(tf.escape_html(link), link)}")

        header = f"🔎 {tf.bold(tf.escape_html(f'Resultados da pesquisa para “{query}”:'))}\n"
        full_text = header + "\n".join(lines)
        tf.send_html(bot, message.chat.id, full_text)
        logging.info(f"Resposta enviada para @{username}: {full_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {full_text}")
    except Exception as e:
        logging.error(f"[ERROR] Handler /search para @{username}: {e}", exc_info=True)
        response_text = tf.escape_html("Ops, algo deu errado no /search. Tente novamente mais tarde.")
        tf.send_html(bot, message.chat.id, response_text)
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")

@bot.message_handler(commands=['ajuda'])
def help_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")
    try:
        itens = [
            "/add - Adiciona um xingamento, mas seja insolente por favor",
            "/list - Lista os xingamentos cadastrados",
            "/remover - Remove um xingamento",
            "/xinga - Envia um xingamento aleatório",
            "/dolar - Exibe a cotação do dólar em reais",
            "/euro - Exibe a cotação do euro em reais",
            "/btc - Exibe a cotação do Bitcoin em dólares",
            "/xmr - Exibe a cotação do Monero em dólares",
            "/real - Comando desnecessário pelo óbvio, mas tente executar pra ver...",
            "/youtube - Exibe resultados de busca de vídeos no YouTube",
            "/search - Exibe resultados de busca no Google",
            "/imagem - Gera uma imagem a partir de um texto (ex.: /imagem porco deitado na grama)"
        ]
        help_text = tf.bold("Comandos disponíveis:") + "\n" + "\n".join(itens)
        tf.send_html(bot, message.chat.id, help_text)
        logging.info(f"Resposta enviada para @{username}: {help_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {help_text}")
    except Exception as e:
        logging.error(f"[ERROR] Falha no comando /ajuda para @{username}: {e}")
        response_text = tf.escape_html("Erro ao exibir ajuda. Tente novamente!")
        tf.send_html(bot, message.chat.id, response_text)
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")

@bot.message_handler(commands=['list'])
def list_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")

    try:
        chat_id = message.chat.id
        logging.debug(f"Verificando tipo de chat para chat_id {chat_id}")
        if message.chat.type == 'private':
            response = tf.escape_markdown_v2('Este comando não pode ser usado em chats privados')
            tf.send_markdown(bot, message.chat.id, response)
            logging.info(f"Resposta enviada para @{username}: {response}")
            logging.debug(f"Resposta completa enviada para @{username}: {response}")
            return

        logging.debug(f"Buscando administradores para chat_id {chat_id}")
        try:
            admins = bot.get_chat_administrators(chat_id)
            admin_ids = [admin.user.id for admin in admins if admin.status != 'creator']
            owner_id = next((admin.user.id for admin in admins if admin.status == 'creator'), None)
            if not owner_id:
                logging.warning(f"Nenhum dono encontrado para chat_id {chat_id}")
                response = tf.escape_markdown_v2('Erro: Não consegui identificar o dono do grupo.')
                tf.send_markdown(bot, message.chat.id, response)
                logging.info(f"Resposta de erro enviada para @{username}: {response}")
                logging.debug(f"Resposta completa de erro enviada para @{username}: {response}")
                return
        except Exception as e:
            logging.error(f"[ERROR] Falha ao buscar administradores para @{username}: {str(e)}", exc_info=True)
            response = tf.escape_markdown_v2('Erro ao verificar permissões. Tente novamente!')
            tf.send_markdown(bot, message.chat.id, response)
            logging.info(f"Resposta de erro enviada para @{username}: {response}")
            logging.debug(f"Resposta completa de erro enviada para @{username}: {response}")
            return

        logging.debug(f"Verificando permissões para user_id {message.from_user.id}")
        if message.from_user.id != owner_id and message.from_user.id not in admin_ids:
            response = tf.escape_markdown_v2('Apenas o administrador e o dono do grupo podem executar este comando')
            tf.send_markdown(bot, message.chat.id, response)
            logging.info(f"Resposta enviada para @{username}: {response}")
            logging.debug(f"Resposta completa enviada para @{username}: {response}")
            return

        logging.debug("Acessando banco de dados frases.db")
        try:
            conn = sqlite3.connect('frases.db')
            c = conn.cursor()
            c.execute("SELECT id, frase FROM frases ORDER BY id DESC LIMIT 10")
            frases = c.fetchall()
            conn.close()
            logging.debug(f"Consulta ao banco retornou {len(frases)} frases")
        except sqlite3.Error as e:
            logging.error(f"[ERROR] Falha ao acessar banco de dados para @{username}: {str(e)}", exc_info=True)
            response = tf.escape_markdown_v2('Erro ao acessar a lista de xingamentos. Tente novamente!')
            tf.send_markdown(bot, message.chat.id, response)
            logging.info(f"Resposta de erro enviada para @{username}: {response}")
            logging.debug(f"Resposta completa de erro enviada para @{username}: {response}")
            return

        if not frases:
            response = tf.escape_markdown_v2('Não há frases cadastradas.')
            tf.send_markdown(bot, message.chat.id, response)
            logging.info(f"Resposta enviada para @{username}: {response}")
            logging.debug(f"Resposta completa enviada para @{username}: {response}")
        else:
            itens = [f"\- {frase[0]}: {tf.escape_markdown_v2(frase[1])}" for frase in frases]
            response = "\n".join([tf.bold_md("Últimos 10 xingamentos cadastrados:")] + itens)
            tf.send_markdown(bot, message.chat.id, response)
            logging.info(f"Resposta enviada para @{username}: {response}")
            logging.debug(f"Resposta completa enviada para @{username}: {response}")

    except Exception as e:
        logging.error(f"[ERROR] Falha inesperada no comando /list para @{username}: {str(e)}", exc_info=True)
        response = tf.escape_markdown_v2('Erro inesperado ao executar o comando /list. Tente novamente!')
        tf.send_markdown(bot, message.chat.id, response)
        logging.info(f"Resposta de erro enviada para @{username}: {response}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response}")

@bot.message_handler(commands=['xinga'])
def random_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")
    conn = sqlite3.connect('frases.db')
    c = conn.cursor()
    c.execute("SELECT frase FROM frases")
    frases = c.fetchall()
    conn.close()

    if not frases:
        response = tf.escape_markdown_v2('Não há frases cadastradas.')
        tf.send_markdown(bot, message.chat.id, response)
        logging.info(f"Resposta enviada para @{username}: {response}")
        logging.debug(f"Resposta completa enviada para @{username}: {response}")
        return

    frase_escolhida = tf.escape_markdown_v2(random.choice(frases)[0])

    if message.reply_to_message and hasattr(message.reply_to_message, 'from_user'):
        target_user = message.reply_to_message.from_user.username or "Unknown"
        response = frase_escolhida
        tf.send_markdown(bot, message.chat.id, response, reply_to_message_id=message.reply_to_message.message_id)
        logging.info(f"Resposta enviada para @{username} (reply): {response}")
        logging.debug(f"Resposta completa enviada para @{username} (reply): {response}")
    else:
        command_parts = message.text.split(maxsplit=2)
        if len(command_parts) > 1 and command_parts[1].startswith('@'):
            response = f"{tf.escape_markdown_v2(command_parts[1])} {frase_escolhida}"
        else:
            response = frase_escolhida
        tf.send_markdown(bot, message.chat.id, response)
        logging.info(f"Resposta enviada para @{username}: {response}")
        logging.debug(f"Resposta completa enviada para @{username}: {response}")

@bot.message_handler(commands=['real'])
def reais_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")
    response = tf.escape_markdown_v2('O real não vale nada, é uma bosta essa moeda estado de merda!')
    tf.send_markdown(bot, message.chat.id, response)
    logging.info(f"Resposta enviada para @{username}: {response}")
    logging.debug(f"Resposta completa enviada para @{username}: {response}")

@bot.message_handler(commands=['euro'])
def euro_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")
    url = 'https://economia.awesomeapi.com.br/all/EUR-BRL'

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        euro_data = response.json()
        valor_euro = euro_data['EUR']['bid']
        response_text = f"O valor atual do euro em reais é {tf.bold_md(f'R$ {valor_euro}')}"
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
    except (requests.exceptions.RequestException, KeyError, ValueError) as e:
        error_msg = f"[ERROR] Euro API para @{username}: {str(e)}"
        logging.error(error_msg)
        response_text = tf.escape_markdown_v2("Erro ao consultar a cotação do euro. Tente novamente!")
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")

@bot.message_handler(commands=['dolar'])
def dolar_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")
    url = 'https://economia.awesomeapi.com.br/all/USD-BRL'

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        dolar_data = response.json()
        valor_dolar = dolar_data['USD']['bid']
        response_text = f"O valor atual do dólar em reais é {tf.bold_md(f'R$ {valor_dolar}')}"
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
    except (requests.exceptions.RequestException, KeyError, ValueError) as e:
        error_msg = f"[ERROR] Dolar API para @{username}: {str(e)}"
        logging.error(error_msg)
        response_text = tf.escape_markdown_v2("Erro ao consultar a cotação do dólar. Tente novamente!")
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")

@bot.message_handler(commands=['btc'])
def bitcoin_price(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")
    url = f"https://rest.coincap.io/v3/assets/bitcoin?apiKey={COINCAP_API_KEY}"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        if 'data' in data and 'priceUsd' in data['data']:
            price = round(float(data['data']['priceUsd']), 2)
            formatted_price = format_price(price)
            response_text = f"Cotação atual do Bitcoin em dólar: {tf.bold_md(f'${formatted_price}')}"
            tf.send_markdown(bot, message.chat.id, response_text)
            logging.info(f"Resposta enviada para @{username}: {response_text}")
            logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
        else:
            error_msg = data.get('data', {}).get('message', 'Resposta inesperada da API')
            response_text = tf.escape_markdown_v2(f"Erro ao obter cotação do Bitcoin: {error_msg}")
            tf.send_markdown(bot, message.chat.id, response_text)
            logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
            logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")

    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response else "Unknown"
        response_text = tf.escape_markdown_v2(f"Erro ao consultar Bitcoin: Problema na API (HTTP {status_code})")
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")
    except requests.exceptions.RequestException as e:
        response_text = tf.escape_markdown_v2("Erro ao consultar Bitcoin: Falha na conexão com a API")
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")
    except (KeyError, TypeError, ValueError) as e:
        response_text = tf.escape_markdown_v2("Erro ao consultar Bitcoin: Resposta inválida da API")
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")
    except Exception as e:
        response_text = tf.escape_markdown_v2("Erro inesperado ao consultar Bitcoin. Tente novamente!")
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")

@bot.message_handler(commands=['xmr'])
def handle_xmr(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")
    url = f"https://rest.coincap.io/v3/assets/monero?apiKey={COINCAP_API_KEY}"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        if 'data' in data and 'priceUsd' in data['data']:
            price = round(float(data['data']['priceUsd']), 2)
            formatted_price = format_price(price)
            response_text = f"Cotação atual do Monero em dólar: {tf.bold_md(f'${formatted_price}')}"
            tf.send_markdown(bot, message.chat.id, response_text)
            logging.info(f"Resposta enviada para @{username}: {response_text}")
            logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
        else:
            error_msg = data.get('data', {}).get('message', 'Resposta inesperada da API')
            response_text = tf.escape_markdown_v2(f"Erro ao obter cotação do Monero: {error_msg}")
            tf.send_markdown(bot, message.chat.id, response_text)
            logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
            logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")

    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response else "Unknown"
        response_text = tf.escape_markdown_v2(f"Erro ao consultar Monero: Problema na API (HTTP {status_code})")
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")
    except requests.exceptions.RequestException as e:
        response_text = tf.escape_markdown_v2("Erro ao consultar Monero: Falha na conexão com a API")
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")
    except (KeyError, TypeError, ValueError) as e:
        response_text = tf.escape_markdown_v2("Erro ao consultar Monero: Resposta inválida da API")
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")
    except Exception as e:
        response_text = tf.escape_markdown_v2("Erro inesperado ao consultar Monero. Tente novamente!")
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")

@bot.message_handler(commands=['add'])
def add_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")
    try:
        frase = message.text.split(' ', 1)[1].strip()
    except IndexError:
        response = tf.escape_markdown_v2('Comando inválido. Use /add e insira o xingamento')
        tf.send_markdown(bot, message.chat.id, response)
        logging.info(f"Resposta enviada para @{username}: {response}")
        logging.debug(f"Resposta completa enviada para @{username}: {response}")
        return

    if len(frase) > 150:
        response = tf.escape_markdown_v2('Xingamento muito longo, por favor use até 150 caracteres')
        tf.send_markdown(bot, message.chat.id, response)
        logging.info(f"Resposta enviada para @{username}: {response}")
        logging.debug(f"Resposta completa enviada para @{username}: {response}")
        return

    insert_frase(frase)
    response = tf.escape_markdown_v2('Xingamento adicionado com sucesso! Seu zuero!')
    tf.send_markdown(bot, message.chat.id, response)
    logging.info(f"Resposta enviada para @{username}: {response}")
    logging.debug(f"Resposta completa enviada para @{username}: {response}")

@bot.message_handler(commands=['remover'])
def remover_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")
    chat_id = message.chat.id
    user_id = message.from_user.id
    if message.chat.type == 'private':
        response = tf.escape_markdown_v2('Este comando não pode ser executado em conversas privadas.')
        tf.send_markdown(bot, message.chat.id, response)
        logging.info(f"Resposta enviada para @{username}: {response}")
        logging.debug(f"Resposta completa enviada para @{username}: {response}")
        return

    admin_ids = [admin.user.id for admin in bot.get_chat_administrators(chat_id) if admin.status != 'creator']
    owner_id = [admin for admin in bot.get_chat_administrators(chat_id) if admin.status == 'creator'][0].user.id
    if user_id != owner_id and user_id not in admin_ids:
        response = tf.escape_markdown_v2('Somente o dono do grupo e administradores podem executar este comando.')
        tf.send_markdown(bot, message.chat.id, response)
        logging.info(f"Resposta enviada para @{username}: {response}")
        logging.debug(f"Resposta completa enviada para @{username}: {response}")
        return

    frase_list = message.text.split()
    if len(frase_list) < 2:
        response = tf.escape_markdown_v2('Insira um ID válido para remover')
        tf.send_markdown(bot, message.chat.id, response)
        logging.info(f"Resposta enviada para @{username}: {response}")
        logging.debug(f"Resposta completa enviada para @{username}: {response}")
        return

    frase_id = frase_list[1]
    if not frase_id.isdigit():
        response = tf.escape_markdown_v2('Insira um ID válido para remover, ID é um número, seu MACACO!')
        tf.send_markdown(bot, message.chat.id, response)
        logging.info(f"Resposta enviada para @{username}: {response}")
        logging.debug(f"Resposta completa enviada para @{username}: {response}")
        return

    conn = sqlite3.connect('frases.db')
    c = conn.cursor()
    c.execute("DELETE FROM frases WHERE ID = ?", (frase_id,))
    conn.commit()
    conn.close()
    response = tf.escape_markdown_v2('Xingamento removido com sucesso!')
    tf.send_markdown(bot, message.chat.id, response)
    logging.info(f"Resposta enviada para @{username}: {response}")
    logging.debug(f"Resposta completa enviada para @{username}: {response}")

@bot.message_handler(func=lambda message: message.chat.type != 'private' and
                    message.text is not None and
                    "boa cabelo" in message.text.lower())
def responder_boa_cabelo(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")
    response = tf.escape_markdown_v2("vlw barba")
    tf.send_markdown(bot, message.chat.id, response, reply_to_message_id=message.message_id)
    logging.info(f"Resposta enviada para @{username}: {response}")
    logging.debug(f"Resposta completa enviada para @{username}: {response}")

@bot.message_handler(commands=['imagem'])
def imagem_command(message):
    username = message.from_user.username or "Unknown"
    prompt = message.text.replace("/imagem", "", 1).strip()
    chat_id = message.chat.id  # Define chat_id
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")

    if not prompt:
        response_text = tf.escape_html(
            "Por favor, forneça uma descrição para a imagem.\n"
            "Exemplo: /imagem porco deitado na grama"
        )
        tf.send_html(bot, message.chat.id, response_text)
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
        return

    # Salva o comando do usuário no chat_memory
    update_chat_memory(message)

    # Gera a imagem na xAI
    try:
        API_URL = "https://api.x.ai/v1/images/generations"
        headers = {
            "Authorization": f"Bearer {XAI_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "grok-2-image",
            "prompt": prompt,
            "n": 1
        }
        resp = requests.post(API_URL, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        image_url = data["data"][0]["url"]
    except Exception as e:
        logging.error(f"[ERROR] Falha ao gerar imagem para @{username}: {str(e)}", exc_info=True)
        response_text = tf.escape_html("❌ Não consegui gerar a imagem. Tente novamente mais tarde.")
        tf.send_html(bot, message.chat.id, response_text)
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")
        return

    # Envia a imagem ao usuário
    try:
        caption = (
            f"🖼️ {tf.italic(tf.escape_html(f'Prompt: {prompt}'))}\n"
            f"{tf.italic(tf.escape_html('Gerada com xAI'))}"
        )
        sent_message = bot.send_photo(
            chat_id=message.chat.id,
            photo=image_url,
            caption=caption,
            parse_mode="HTML",
            reply_to_message_id=message.message_id
        )
        # Salva a legenda da resposta no chat_memory
        update_chat_memory(sent_message)
        last_image_prompt[chat_id] = prompt
        logging.info(f"Imagem enviada para @{username} (prompt: {prompt})")
        logging.debug(f"Imagem completa enviada para @{username} (prompt: {prompt}, caption: {caption})")
    except Exception as e:
        logging.error(f"[ERROR] Falha ao enviar imagem para @{username}: {str(e)}", exc_info=True)
        response_text = tf.escape_html("❌ Erro ao enviar a imagem. Verifique a conexão ou tente novamente.")
        tf.send_html(bot, message.chat.id, response_text)
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")

# Inicia o bot
try:
    logging.info("Iniciando polling do bot...")
    bot.polling()
except Exception as e:
    logging.error(f"[ERROR] Falha crítica no polling: {str(e)}")
    raise
