import configparser
import os
import time
import random
import sqlite3
import requests
import sys
import re
import logging
import logging.handlers
import urllib.parse
from typing import Tuple, Optional

# Adiciona o caminho pras bibliotecas locais, caso tu tenha instalado algo fora do padrão
sys.path.append('/home/morfetico/.local/lib/python3.12/site-packages/')
import telebot
import shutil
import openai
from openai import OpenAIError

# Variáveis globais pra controlar o rate limit de requisições
start_time = time.time()
request_count = 0

# Configuração do logging - aqui é onde a mágica dos logs acontece!
# Primeiro, criamos um logger temporário pra capturar qualquer problema na inicialização
temp_logger = logging.getLogger('temp_init')
temp_handler = logging.FileHandler('bot-telegram.log', mode='a')  # Modo 'a' pra appending
temp_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
temp_logger.addHandler(temp_handler)
temp_logger.setLevel(logging.DEBUG)
temp_logger.debug("Iniciando configuração do logging - se tu tá vendo isso, o log tá funcionando!")

# Filtro pra esconder tokens sensíveis (tipo o do Telegram) no log
class TokenObfuscationFilter(logging.Filter):
    def __init__(self):
        super().__init__()
        # Regex pra pegar tokens do Telegram (formato: <bot_id>:<token>)
        self.token_pattern = r'\b\d{8,10}:[A-Za-z0-9_-]{35}\b'

    def filter(self, record):
        try:
            # Pega a mensagem do log
            msg = record.getMessage()
            # Troca qualquer token por '****:****' pra não vazar
            record.msg = re.sub(self.token_pattern, '****:****', msg)
            # Se tiver argumentos no log, mascara eles também
            if hasattr(record, 'args') and record.args:
                record.args = tuple(
                    re.sub(self.token_pattern, '****:****', str(arg))
                    if isinstance(arg, str) else arg
                    for arg in record.args
                )
            return True
        except Exception as e:
            # Se der zica no filtro, loga o erro mas deixa o log passar
            temp_logger.error(f"Erro no TokenObfuscationFilter: {str(e)}")
            return True

# Configuração do log principal - aqui é onde definimos como o bot vai logar tudo
try:
    # Verifica se o arquivo de log existe e seta permissões pra escrita
    log_file = 'bot-telegram.log'
    if not os.path.exists(log_file):
        with open(log_file, 'a'):
            os.chmod(log_file, 0o666)  # Permissão pra todo mundo escrever, só pra garantir
    else:
        os.chmod(log_file, 0o666)

    # Lê o bot-telegram.cfg, ignorando linhas com # ou ;
    config_bot = configparser.ConfigParser(comment_prefixes=('#', ';'))
    config_bot.optionxform = str  # Mantém maiúsculas/minúsculas nas chaves
    if not config_bot.read('bot-telegram.cfg'):
        temp_logger.error("Erro: Não conseguiu ler o bot-telegram.cfg! Usando LOG_LEVEL=INFO como padrão.")
        LOG_LEVEL = 'INFO'
    else:
        LOG_LEVEL = config_bot['DEFAULT'].get('LOG_LEVEL', 'INFO').upper()
        temp_logger.debug(f"Configuração lida do bot-telegram.cfg: LOG_LEVEL={LOG_LEVEL}, BOT_AI={config_bot['DEFAULT'].get('BOT_AI', 'xai')}, XAI_MODEL={config_bot['DEFAULT'].get('XAI_MODEL', 'grok-3-mini-fast-beta')}")

    # Valida o LOG_LEVEL pra não dar zica
    valid_log_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
    if LOG_LEVEL not in valid_log_levels:
        temp_logger.error(f"LOG_LEVEL inválido: {LOG_LEVEL}. Usando INFO como padrão.")
        LOG_LEVEL = 'INFO'

    # Cria um logger específico pro bot, pra não misturar com logs de outras bibliotecas
    logger = logging.getLogger('telegram_bot')
    logger.setLevel(getattr(logging, LOG_LEVEL))

    # Limpa qualquer handler do logger raiz pra evitar conflitos (tipo com telebot)
    logging.getLogger('').handlers = []

    # Configura o handler pro arquivo de log
    handler = logging.FileHandler(log_file, mode='a')
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    handler.addFilter(TokenObfuscationFilter())
    handler.setLevel(getattr(logging, LOG_LEVEL))
    logger.addHandler(handler)

    # Tira o handler temporário, agora que o logger principal tá de pé
    temp_logger.removeHandler(temp_handler)
    temp_handler.close()

    logger.info("Bot iniciado - pronto pra zuar e logar tudo direitinho!")
except Exception as e:
    temp_logger.error(f"Erro na configuração do logging: {str(e)}")
    raise

# Substitui o logging padrão pelo nosso logger, pra todas as chamadas usarem telegram_bot
logging = logger

# Configuração do rate limit - pra não sobrecarregar a API
REQUEST_LIMIT = 40
TIME_WINDOW = 60
request_count = 0
start_time = time.time()

# Configuração do Telegram - lê o token do bot
config_telegram = configparser.ConfigParser()
config_telegram.read('token-telegram.cfg')
TOKEN = config_telegram['DEFAULT']['TOKEN']
bot = telebot.TeleBot(TOKEN)
logging.debug("Token do Telegram lido com sucesso (mascarado no log, claro!)")

# Configuração da CoinCap API - pra cotações de cripto
config_coincap = configparser.ConfigParser()
config_coincap.read('token-coincap.cfg')
COINCAP_API_KEY = config_coincap['DEFAULT']['TOKEN']
logging.debug("Token da CoinCap API lido com sucesso")

# Limite de caracteres do Telegram - pra não estourar nas mensagens
TELEGRAM_MAX_CHARS = 4096

# Função pra escapar caracteres especiais pro MarkdownV2 do Telegram
def escape_markdown_v2(text):
    r"""
    Escapa caracteres reservados pro Telegram MarkdownV2, mas preserva blocos de código e LaTeX.
    Suporta \( ... \), \[ ... \], $$ ... $$, e trata \ como literal fora de blocos matemáticos.
    Simplificado pra evitar erros com blocos LaTeX.
    """
    reserved_chars = "_*[]()~`>#+-=|{}.!"  # Caracteres que o Telegram exige escapar
    result = ""
    i = 0
    length = len(text)
    code_block_open = False
    inline_code_open = False
    math_inline_open = False
    math_display_open = False

    while i < length:
        # Blocos de código com ```
        if i + 2 < length and text[i:i+3] == "```":
            code_block_open = not code_block_open
            result += "```"
            i += 3
            while i < length and text[i] != "\n":
                result += text[i]
                i += 1
            if i < length:
                result += text[i]
                i += 1
            continue

        # Código inline com `
        if text[i] == "`" and not code_block_open:
            inline_code_open = not inline_code_open
            result += "`"
            i += 1
            continue

        # Expressões matemáticas inline com $$  ...  $$
        if i + 1 < length and text[i:i+2] == r"$$ " and not (code_block_open or inline_code_open):
            math_inline_open = True
            result += r"\("
            i += 2
            continue
        if i + 1 < length and text[i:i+2] == r" $$" and not (code_block_open or inline_code_open) and math_inline_open:
            math_inline_open = False
            result += r"\)"
            i += 2
            continue

        # Expressões matemáticas display com $$   ...   $$
        if i + 1 < length and text[i:i+2] == "$$  " and not (code_block_open or inline_code_open):
            math_display_open = not math_display_open
            result += "  $$"
            i += 2
            continue

        # Trata barras invertidas (\ vira \\) fora de blocos matemáticos/código
        if text[i] == "\\" and not (code_block_open or inline_code_open or math_inline_open or math_display_open):
            result += r"\\"
            i += 1
            continue

        # Escapa caracteres reservados fora de blocos protegidos
        if text[i] in reserved_chars and not (code_block_open or inline_code_open or math_inline_open or math_display_open):
            result += "\\" + text[i]
        else:
            result += text[i]
        i += 1

    # Fecha blocos que ficaram abertos
    if code_block_open:
        result += "```"
    if math_inline_open:
        result += r"\)"
    if math_display_open:
        result += "$$"

    # Corta a mensagem se passar do limite do Telegram
    if len(result) > TELEGRAM_MAX_CHARS:
        result = result[:TELEGRAM_MAX_CHARS - 3] + "..."
        if code_block_open:
            result += "```"
        if math_inline_open:
            result += r"\)"
        if math_display_open:
            result += "$$  "

    logging.debug(f"Texto escapado para MarkdownV2: {result[:100]}...")
    return result if result else "Erro ao processar formatação, tente novamente."

# Armazenamento em memória - pra guardar infos dos usuários e histórico de chat
stored_info = {}
chat_memory = {}
last_image_prompt = {}  # Guarda o último prompt de imagem por chat_id

# Funções pra gerenciar o banco de dados de frases (xingamentos)
def create_table():
    """Cria a tabela de frases no SQLite, se não existir."""
    with sqlite3.connect('frases.db') as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS frases (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            frase TEXT NOT NULL
        );
        """)
    logging.debug("Tabela de frases criada ou verificada no banco frases.db")

def insert_frase(frase: str):
    """Insere uma nova frase no banco."""
    with sqlite3.connect('frases.db') as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO frases (frase) VALUES (?)", (frase,))
    logging.debug(f"Frase inserida no banco: {frase}")

def get_random_frase() -> str:
    """Pega uma frase aleatória do banco."""
    with sqlite3.connect('frases.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT frase FROM frases ORDER BY RANDOM() LIMIT 1")
        result = cursor.fetchone()
        return result[0] if result else None
    logging.debug("Frase aleatória buscada no banco")

# Funções pra gerenciar memória de conversa
def update_chat_memory(message):
    """Atualiza o histórico de conversa, mantendo até 10 mensagens."""
    chat_id = message.chat.id
    if chat_id not in chat_memory:
        chat_memory[chat_id] = []

    role = "user" if message.from_user.id != bot.get_me().id else "assistant"
    content = message.text or message.caption or "[Imagem]"
    chat_memory[chat_id].append({"role": role, "content": content})

    if len(chat_memory[chat_id]) > 10:
        chat_memory[chat_id] = chat_memory[chat_id][-10:]
    logging.debug(f"Memória de chat atualizada para chat_id {chat_id}")

def get_chat_history(message, reply_limit: int = 6) -> list:
    """Pega o histórico de conversa, incluindo respostas encadeadas."""
    chat_id = message.chat.id
    history = []

    if message.reply_to_message:
        current_message = message
        content = current_message.text or current_message.caption or "[Imagem]"
        history.append({"role": "user", "content": content})
        while current_message.reply_to_message and len(history) < reply_limit + 1:
            previous_message = current_message.reply_to_message
            role = "assistant" if previous_message.from_user.id == bot.get_me().id else "user"
            content = previous_message.text or previous_message.caption or "[Imagem]"
            history.append({"role": role, "content": content})
            current_message = previous_message
        history.reverse()
    else:
        if chat_id in chat_memory:
            history = chat_memory[chat_id].copy()

    token_count = count_tokens(history)
    while token_count > MAX_TOKENS * 0.8:  # Aumenta o limite de tokens
        history.pop(0)
        token_count = count_tokens(history)

    logging.debug(f"Histórico de chat obtido para chat_id {chat_id}: {len(history)} mensagens")
    return history

def clear_stored_info(user_id):
    """Limpa as infos armazenadas de um usuário."""
    if user_id in stored_info:
        del stored_info[user_id]
        logging.info(f"Informações armazenadas limpas para user_id {user_id}")

# Cria a tabela de frases no banco ao iniciar
create_table()

# Configuração da OpenAI - pra usar o ChatGPT se configurado
config_openai = configparser.ConfigParser()
config_openai.read('token-openai.cfg')
OPENAI_API_KEY = config_openai['DEFAULT']['API_KEY']
logging.debug("Token da OpenAI lido com sucesso")

# Configuração da xAI - pra usar o Grok
config_xai = configparser.ConfigParser()
config_xai.read('token-xai.cfg')
XAI_API_KEY = config_xai['DEFAULT']['API_KEY']
logging.debug("Token da xAI lido com sucesso")

# Configuração do bot - define se usa OpenAI ou xAI
BOT_AI = config_bot['DEFAULT'].get('BOT_AI', 'openai')
XAI_MODEL = config_bot['DEFAULT'].get('XAI_MODEL', 'grok-2-latest')
logging.debug(f"Configuração do bot: BOT_AI={BOT_AI}, XAI_MODEL={XAI_MODEL}")

# Parâmetros gerais pra IA
MAX_TOKENS = 1000  # Limite de tokens pra respostas
TEMPERATURE = 0.8  # Controla a criatividade da IA
OPENAI_MODEL = "gpt-4"  # Modelo padrão do OpenAI

def count_tokens(messages):
    """Conta tokens pras mensagens, pra não estourar o limite."""
    total_tokens = 0
    for msg in messages:
        content = msg["content"] or "[Imagem]"
        total_tokens += len(content.split()) + 10
    logging.debug(f"Contagem de tokens: {total_tokens}")
    return total_tokens

def get_prompt() -> str:
    r"""Lê o prompt base do prompt.cfg ou usa um padrão."""
    try:
        with open('prompt.cfg', 'r', encoding='utf-8') as arquivo:
            base_prompt = arquivo.read().strip()
            logging.debug("Prompt lido do prompt.cfg")
    except FileNotFoundError:
        base_prompt = ("Você é um assistente útil e respeitoso em um bot do Telegram. "
                       "Responda de forma clara, amigável e profissional, mantendo o contexto da conversa. "
                       "Evite respostas ofensivas ou inadequadas. "
                       "Se a mensagem for curta (menos de 15 caracteres) ou vaga, peça mais detalhes com base no histórico da conversa. "
                       "Quando o usuário pedir para 'armazenar a info', guarde a informação em uma lista associada ao ID do usuário. "
                       "Quando perguntado 'quais são as infos que te pedi pra armazenar?', responda com a lista de informações armazenadas. "
                       "Se o usuário pedir LaTeX, use o formato de blocos matemáticos do Telegram (  $$ ... $$   para display, \\( ... \\) para inline). "
                       "Se pedir 'code LaTeX do Telegram', retorne o código LaTeX puro dentro de um bloco de código ```latex ... ```. "
                       "Se a pergunta for sobre uma imagem gerada (ex.: 'quem são esses?'), explique que você não vê a imagem, mas pode descrever o que tentou gerar com base no prompt de texto fornecido.")
        logging.warning("prompt.cfg não encontrado, usando prompt padrão")
    return f"{base_prompt}\n\nSua resposta deve ter no máximo 4000 caracteres para caber no limite do Telegram (4096 caracteres, incluindo formatação). Se necessário, resuma ou ajuste o conteúdo para não ultrapassar esse limite."

# Handler pro comando /xinga - manda um xingamento aleatório
@bot.message_handler(commands=['xinga'])
def random_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    conn = sqlite3.connect('frases.db')
    c = conn.cursor()
    c.execute("SELECT frase FROM frases")
    frases = c.fetchall()
    conn.close()

    if not frases:
        response = 'Não há frases cadastradas.'
        logging.info(f"Resposta para @{username}: {response}")
        bot.send_message(message.chat.id, response)
        return

    frase_escolhida = random.choice(frases)[0]

    if message.reply_to_message and hasattr(message.reply_to_message, 'from_user'):
        target_user = message.reply_to_message.from_user.username or "Unknown"
        response = frase_escolhida
        logging.info(f"Resposta para @{target_user} (reply via @{username}): {response}")
        bot.reply_to(message.reply_to_message, response)
    else:
        command_parts = message.text.split(maxsplit=2)
        if len(command_parts) > 1 and command_parts[1].startswith('@'):
            response = "{} {}".format(command_parts[1], frase_escolhida)
        else:
            response = frase_escolhida
        logging.info(f"Resposta para @{username}: {response}")
        bot.send_message(message.chat.id, response)

# Handler principal pra conversar com a IA
@bot.message_handler(func=lambda message: message.text is not None and
                    message.from_user.id != bot.get_me().id and
                    (bot.get_me().username in message.text or
                     (message.reply_to_message is not None and
                      message.reply_to_message.from_user.id == bot.get_me().id)))
def responder(message):
    global start_time, request_count
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text or '[No text]'}")

    # Bloqueia chats privados e bots
    if message.chat.type == 'private' or message.from_user.is_bot:
        response_text = "Desculpe, só respondo em grupos e não a bots!"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
        return

    # Verifica se tem texto na mensagem
    if not message.text:
        response_text = "Desculpe, não posso processar mensagens sem texto! Tente enviar uma mensagem com texto."
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.reply_to(message, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
        return

    # Ignora mensagens muito curtas ou irrelevantes
    text_lower = message.text.lower().strip()
    if len(text_lower) < 5 or text_lower in ["ok", "sim", "não", "ta", "blz", "valeu"]:
        response_text = "Beleza, mas me dá mais contexto ou pergunta algo mais específico!"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.reply_to(message, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
        return

    # Rate limit pra não fritar a API
    current_time = time.time()
    if current_time - start_time > TIME_WINDOW:
        start_time = current_time
        request_count = 0
    if request_count >= REQUEST_LIMIT:
        response_text = "Tô de boa, mas muito requisitado agora! Tenta de novo em uns segundos."
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
        return
    request_count += 1

    # Verifica se é uma mensagem curta após uma imagem
    chat_id = message.chat.id
    if len(text_lower) < 15 and chat_id in last_image_prompt:
        response_text = f"Sua mensagem tá meio curta! Você tá falando da imagem gerada com o prompt '{last_image_prompt[chat_id]}'? Eu não vejo a imagem, mas posso descreva algo sobre esse tema ou responder algo mais específico se tu explicar melhor!"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.reply_to(message, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
        return

    update_chat_memory(message)
    chat_history = get_chat_history(message, reply_limit=6)
    system_prompt = get_prompt()
    user_id = message.from_user.id

    # Trata pedidos específicos sobre barras invertidas
    if "manda" in text_lower and (r"\\" in message.text or "backslash" in text_lower):
        response_text = r"Beleza, entendi! Vou usar `\` pra barras invertidas fora de blocos LaTeX, e `\` dentro de expressões matemáticas como `$$\sqrt{-1}$$`. Se precisar de algo específico, explica mais!"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.reply_to(message, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
        return

    # Trata pedidos de "code LaTeX do Telegram"
    if "code latex" in text_lower and "telegram" in text_lower:
        response_text = r"```latex\n% Fórmula de Euler\n\documentclass{article}\n\usepackage{amsmath}\n\n\begin{document}\n\nA Fórmula de Euler é \\[ e^{i\pi} + 1 = 0 \\].\n\nEm geral: \\[ e^{i\theta} = \cos(\theta) + i \sin(\theta) \\]\n\n\end{document}\n```"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.reply_to(message, response_text, parse_mode='MarkdownV2')
        return

    # Trata perguntas sobre imagens geradas
    if any(keyword in text_lower for keyword in ["quem são", "quem é", "o que é isso", "o que são"]) and chat_id in last_image_prompt:
        response_text = f"Eu não vejo a imagem gerada, mas ela foi criada com base no prompt: '{last_image_prompt[chat_id]}'. Quer que eu descreva algo específico sobre esse tema ou gere outra imagem?"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.reply_to(message, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
        return

    # Armazena infos do usuário
    if "armazene" in text_lower and ("info" in text_lower or "armazene" in text_lower.split()):
        try:
            info = message.text.split("armazene", 1)[1].replace("a info:", "").strip()
            if not info:
                response_text = "Opa, amigo! Armazenar o quê? Me dá algo pra guardar!"
                logging.info(f"Resposta para @{username}: {response_text}")
                bot.reply_to(message, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
                return
            if user_id not in stored_info:
                stored_info[user_id] = []
            stored_info[user_id].append(info)
            logging.info(f"Info armazenada para user_id {user_id} de @{username}: {info}")
            response_text = "Beleza, guardei a info pra você!"
            logging.info(f"Resposta para @{username}: {response_text}")
            bot.reply_to(message, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
            return
        except IndexError:
            response_text = "Opa, amigo! Armazenar o quê? Me dá algo pra guardar!"
            logging.info(f"Resposta para @{username}: {response_text}")
            bot.reply_to(message, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
            return

    # Lista infos armazenadas
    if "quais são as infos que te pedi pra armazenar?" in text_lower or \
       "me diga o que pedi pra armazenar" in text_lower:
        if user_id in stored_info and stored_info[user_id]:
            resposta = "Olha só o que você me pediu pra guardar:\n" + "\n".join(stored_info[user_id])
        else:
            resposta = "Você ainda não me passou nenhuma info pra guardar, amigo!"
        logging.info(f"Resposta para @{username}: {resposta}")
        bot.reply_to(message, escape_markdown_v2(resposta), parse_mode='MarkdownV2')
        return

    # Limpa infos armazenadas
    if "limpe tudo que armazenou" in text_lower:
        clear_stored_info(user_id)
        response_text = "Feito, amigo! Tudo limpo, não guardei mais nada."
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.reply_to(message, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
        return

    # Adiciona infos armazenadas ao prompt, se existirem
    if user_id in stored_info:
        system_prompt += f"\nInformações que esse usuário me pediu pra guardar: {', '.join(stored_info[user_id])}"
    # Contexto pra barras invertidas
    system_prompt += r"\nQuando mencionar barras invertidas, use `\` para texto literal fora de LaTeX, e `\` dentro de blocos matemáticos como `$$\sqrt{-1}$$`."

    messages = [{"role": "system", "content": system_prompt}] + chat_history

    try:
        start_time = time.time()
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Chama a API configurada (OpenAI ou xAI)
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
                    logging.debug("Resposta gerada pela OpenAI")
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
                    logging.debug("Resposta gerada pela xAI")
                else:
                    raise ValueError(f"Configuração inválida para BOT_AI: {BOT_AI}. Use 'openai' ou 'xai'.")
                break  # Sai do loop se a requisição der certo
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    logging.warning(f"Tentativa {attempt + 1} falhou com erro: {str(e)}. Tentando novamente...")
                    time.sleep(2 ** attempt)  # Backoff exponencial
                    continue
                raise  # Levanta o erro na última tentativa

        response_time = time.time() - start_time
        if not answer or len(answer) < 10:
            logging.warning(f"Resposta inválida da API para @{username}: {answer}")
            answer = "Opa, não consegui processar direito sua instrução. Tenta explicar de novo ou pedir algo diferente!"

        logging.info(f"Resposta gerada em {response_time:.2f}s usando {BOT_AI} para @{username}: {answer[:100]}...")

        chat_memory[chat_id].append({"role": "assistant", "content": answer})
        answer_escaped = escape_markdown_v2(answer)
        logging.debug(f"Texto antes do escape para @{username}: {answer[:100]}...")
        logging.debug(f"Texto após escape para @{username}: {answer_escaped[:100]}...")

        # Tenta enviar a resposta com MarkdownV2
        try:
            if message.reply_to_message:
                bot.reply_to(message, answer_escaped, parse_mode='MarkdownV2')
            else:
                bot.send_message(message.chat.id, answer_escaped, parse_mode='MarkdownV2')
        except Exception as e:
            error_msg = f"[ERROR] Falha ao enviar com MarkdownV2 para @{username}: {str(e)}"
            logging.error(error_msg)
            # Fallback: tenta sem LaTeX
            try:
                simplified_answer = answer.replace(r'\[', '').replace(r' $$', '').replace(r'$$ ', '').replace(r' $$', '').replace(' \]', '')
                simplified_escaped = escape_markdown_v2(simplified_answer)
                if message.reply_to_message:
                    bot.reply_to(message, simplified_escaped, parse_mode='MarkdownV2')
                else:
                    bot.send_message(message.chat.id, simplified_escaped, parse_mode='MarkdownV2')
                logging.info(f"Resposta enviada com MarkdownV2 simplificado para @{username}: {simplified_answer[:100]}...")
            except Exception as e2:
                # Último fallback: envia sem formatação
                error_msg = f"[ERROR] Falha no fallback MarkdownV2 para @{username}: {str(e2)}"
                logging.error(error_msg)
                try:
                    if message.reply_to_message:
                        bot.reply_to(message, answer, parse_mode=None)
                    else:
                        bot.send_message(message.chat.id, answer, parse_mode=None)
                    logging.info(f"Resposta enviada sem formatação para @{username}: {answer[:100]}...")
                except Exception as e3:
                    error_msg = f"[ERROR] Falha no fallback final para @{username}: {str(e3)}"
                    logging.error(error_msg)
                    response_text = "Deu uma zica aqui, brother! Tenta depois!"
                    bot.send_message(message.chat.id, response_text)
                    logging.info(f"Resposta para @{username}: {response_text}")

    except OpenAIError as e:
        error_msg = f"[ERROR] Erro na API da OpenAI para @{username}: {str(e)}"
        logging.error(error_msg)
        response_text = "Ops, minha cabeça de IA deu tilt! Tenta de novo!"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
    except requests.exceptions.RequestException as e:
        error_msg = f"[ERROR] Erro na API da xAI para @{username}: {str(e)}"
        logging.error(error_msg)
        response_text = "Ops, deu problema com a xAI! Tenta de novo!"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
    except Exception as e:
        error_msg = f"[ERROR] Inesperado para @{username}: {str(e)}"
        logging.error(error_msg)
        response_text = "Deu uma zica aqui, brother! Tenta depois!"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, escape_markdown_v2(response_text), parse_mode='MarkdownV2')

# Handler pra busca no YouTube
@bot.message_handler(commands=['youtube'])
def youtube_search_command(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    query = message.text.replace("/youtube", "").strip()
    if not query:
        response = "Por favor, execute o /youtube com algum termo de busca"
        logging.info(f"Resposta para @{username}: {response}")
        bot.send_message(chat_id=message.chat.id, text=response)
        return

    API_KEY = open("token-google.cfg").read().strip()
    search_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&q={query}&type=video&maxResults=5&key={API_KEY}"

    try:
        response = requests.get(search_url)
        response.raise_for_status()
        data = response.json()

        items = data.get("items", [])
        if not items:
            response_text = "Não foram encontrados resultados para a sua pesquisa."
            logging.info(f"Resposta para @{username}: {response_text}")
            bot.send_message(chat_id=message.chat.id, text=response_text)
            return

        resposta = f"🔎 Resultados do YouTube para *{query}*:\n\n"
        for item in items:
            video_id = item["id"].get("videoId")
            titulo = item["snippet"].get("title")
            url = f"https://www.youtube.com/watch?v={video_id}"
            resposta += f"- {titulo}\n{url}\n\n"

        logging.info(f"Resposta para @{username}: {resposta.strip()}")
        bot.send_message(chat_id=message.chat.id, text=resposta.strip())

    except requests.exceptions.RequestException as e:
        error_msg = f"[ERROR] YouTube API para @{username}: {str(e)}"
        logging.error(error_msg)
        response_text = "Erro ao acessar a API do YouTube."
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(chat_id=message.chat.id, text=response_text)
    except Exception as e:
        error_msg = f"[ERROR] Inesperado /youtube para @{username}: {str(e)}"
        logging.error(error_msg)
        response_text = "Erro inesperado ao buscar no YouTube."
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(chat_id=message.chat.id, text=response_text)

# Handler pra busca no Google
@bot.message_handler(commands=['search'])
def search_command(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    query = message.text.replace("/search", "").strip()
    if not query:
        response = "Por favor, execute o /search com algum termo de busca"
        logging.info(f"Resposta para @{username}: {response}")
        bot.send_message(chat_id=message.chat.id, text=response)
        return

    API_KEY = open("token-google.cfg").read().strip()
    SEARCH_ENGINE_ID = open("token-google-engine.cfg").read().strip()
    search_url = f"https://www.googleapis.com/customsearch/v1?key={API_KEY}&cx={SEARCH_ENGINE_ID}&q={query}"

    try:
        results = requests.get(search_url).json()
        if results.get("searchInformation").get("totalResults") == "0":
            response = "Não foram encontrados resultados para a sua pesquisa."
            logging.info(f"Resposta para @{username}: {response}")
            bot.send_message(chat_id=message.chat.id, text=response)
            return
        results = results["items"]
        response = "Resultados da pesquisa para '" + query + "': \n"
        for result in results[:5]:
            response += result["title"] + " - " + result["link"] + "\n"
        logging.info(f"Resposta para @{username}: {response}")
        bot.send_message(chat_id=message.chat.id, text=response)
    except (requests.exceptions.RequestException, KeyError) as e:
        error_msg = f"[ERROR] Google API para @{username}: {str(e)}"
        logging.error(error_msg)
        response = "Desculpe, ocorreu um erro ao acessar a API do Google. Por favor, tente novamente mais tarde."
        logging.info(f"Resposta para @{username}: {response}")
        bot.send_message(chat_id=message.chat.id, text=response)

# Handlers pras cotações de moedas
@bot.message_handler(commands=['real'])
def reais_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    response = 'O real não vale nada, é uma bosta essa moeda estado de merda!'
    logging.info(f"Resposta para @{username}: {response}")
    bot.send_message(message.chat.id, response)

@bot.message_handler(commands=['euro'])
def euro_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    url = 'https://economia.awesomeapi.com.br/all/EUR-BRL'

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        euro_data = response.json()
        valor_euro = euro_data['EUR']['bid']
        response_text = f"O valor atual do euro em reais é R$ {valor_euro}"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, response_text)
    except (requests.exceptions.RequestException, KeyError, ValueError) as e:
        error_msg = f"[ERROR] Euro API para @{username}: {str(e)}"
        logging.error(error_msg)
        response_text = "Erro ao consultar a cotação do euro. Tente novamente!"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, response_text)

@bot.message_handler(commands=['dolar'])
def dolar_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    url = 'https://economia.awesomeapi.com.br/all/USD-BRL'

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        dolar_data = response.json()
        valor_dolar = dolar_data['USD']['bid']
        response_text = f"O valor atual do dólar em reais é R$ {valor_dolar}"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, response_text)
    except (requests.exceptions.RequestException, KeyError, ValueError) as e:
        error_msg = f"[ERROR] Dolar API para @{username}: {str(e)}"
        logging.error(error_msg)
        response_text = "Erro ao consultar a cotação do dólar. Tente novamente!"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, response_text)

# Função auxiliar pra formatar preços de cripto
def format_price(price: float) -> str:
    """Formata preço com duas casas decimais, usando vírgula como separador."""
    return f"{price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# Handlers pras cotações de criptomoedas
@bot.message_handler(commands=['btc'])
def bitcoin_price(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    url = f"https://rest.coincap.io/v3/assets/bitcoin?apiKey={COINCAP_API_KEY}"
    log_url = "https://rest.coincap.io/v3/assets/bitcoin?apiKey=****"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        if 'data' in data and 'priceUsd' in data['data']:
            price = round(float(data['data']['priceUsd']), 2)
            formatted_price = format_price(price)
            response_text = f"Cotação atual do Bitcoin em dólar: ${formatted_price}"
            logging.info(f"Resposta para @{username}: {response_text}")
            bot.send_message(message.chat.id, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
        else:
            error_msg = data.get('data', {}).get('message', 'Resposta inesperada da API')
            logging.error(f"[ERROR] /btc para @{username}: {error_msg}")
            response_text = f"Erro ao obter cotação do Bitcoin: {error_msg}"
            logging.info(f"Resposta para @{username}: {response_text}")
            bot.send_message(message.chat.id, escape_markdown_v2(response_text), parse_mode='MarkdownV2')

    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response else "Unknown"
        logging.error(f"[ERROR] HTTP /btc para @{username}: Status: {status_code}")
        response_text = f"Erro ao consultar Bitcoin: Problema na API (HTTP {status_code})"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
    except requests.exceptions.RequestException as e:
        logging.error(f"[ERROR] Rede /btc para @{username}: {str(e)}")
        response_text = "Erro ao consultar Bitcoin: Falha na conexão com a API"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
    except (KeyError, TypeError, ValueError) as e:
        logging.error(f"[ERROR] Parsing /btc para @{username}: {str(e)}, Resposta: [redacted]")
        response_text = "Erro ao consultar Bitcoin: Resposta inválida da API"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
    except Exception as e:
        logging.error(f"[ERROR] Inesperado /btc para @{username}: {str(e)}")
        response_text = "Erro inesperado ao consultar Bitcoin. Tente novamente!"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, escape_markdown_v2(response_text), parse_mode='MarkdownV2')

@bot.message_handler(commands=['xmr'])
def handle_btc(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    url = f"https://rest.coincap.io/v3/assets/monero?apiKey={COINCAP_API_KEY}"
    log_url = "https://rest.coincap.io/v3/assets/monero?apiKey=****"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        if 'data' in data and 'priceUsd' in data['data']:
            price = round(float(data['data']['priceUsd']), 2)
            formatted_price = format_price(price)
            response_text = f"Cotação atual do Monero em dólar: ${formatted_price}"
            logging.info(f"Resposta para @{username}: {response_text}")
            bot.send_message(message.chat.id, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
        else:
            error_msg = data.get('data', {}).get('message', 'Resposta inesperada da API')
            logging.error(f"[ERROR] /xmr para @{username}: {error_msg}")
            response_text = f"Erro ao obter cotação do Monero: {error_msg}"
            logging.info(f"Resposta para @{username}: {response_text}")
            bot.send_message(message.chat.id, escape_markdown_v2(response_text), parse_mode='MarkdownV2')

    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response else "Unknown"
        logging.error(f"[ERROR] HTTP /xmr para @{username}: Status: {status_code}")
        response_text = f"Erro ao consultar Monero: Problema na API (HTTP {status_code})"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
    except requests.exceptions.RequestException as e:
        logging.error(f"[ERROR] Rede /xmr para @{username}: {str(e)}")
        response_text = "Erro ao consultar Monero: Falha na conexão com a API"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
    except (KeyError, TypeError, ValueError) as e:
        logging.error(f"[ERROR] Parsing /xmr para @{username}: {str(e)}, Resposta: [redacted]")
        response_text = "Erro ao consultar Monero: Resposta inválida da API"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
    except Exception as e:
        logging.error(f"[ERROR] Inesperado /xmr para @{username}: {str(e)}")
        response_text = "Erro inesperado ao consultar Monero. Tente novamente!"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, escape_markdown_v2(response_text), parse_mode='MarkdownV2')

# Handlers pra gerenciar frases e ajuda
@bot.message_handler(commands=['ajuda'])
def help_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    help_text = 'Comandos disponíveis:\n'
    help_text += '/add - Adiciona um xingamento, mas seja insolente por favor\n'
    help_text += '/list - Lista os xingamentos cadastrados\n'
    help_text += '/remover - Remove um xingamento\n'
    help_text += '/xinga - Envia um xingamento aleatório\n'
    help_text += '/dolar - Exibe a cotação do dolar em reais\n'
    help_text += '/euro - Exibe a cotação do euro em reais\n'
    help_text += '/btc - Exibe a cotação do Bitcoin em dolares\n'
    help_text += '/xmr - Exibe a cotação do Monero em dolares\n'
    help_text += '/real - Comando desnecessário pelo óbvio, mas tente executar pra ver...\n'
    help_text += '/youtube - Exibe resultados de busca de vídeos no Youtube\n'
    help_text += '/search - Exibe resultados de busca no Google\n'
    help_text += '/imagem - Gera uma imagem a partir de um texto (ex.: /imagem porco deitado na grama)'
    logging.info(f"Resposta para @{username}: {help_text}")
    bot.send_message(message.chat.id, help_text)

@bot.message_handler(commands=['add'])
def add_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    text = message.text.split()
    if len(text) < 2:
        response = 'Comando inválido. Use /add e insira o xingamento'
        logging.info(f"Resposta para @{username}: {response}")
        bot.send_message(message.chat.id, response)
        return
    frase = text[1]
    if len(message.text.split(' ', 1)[1]) > 150:
        response = 'Xingamento muito longo, por favor use até 150 caracteres'
        logging.info(f"Resposta para @{username}: {response}")
        bot.send_message(message.chat.id, response)
        return
    conn = sqlite3.connect('frases.db')
    c = conn.cursor()
    frase = message.text.split(' ', 1)[1]
    c.execute("INSERT INTO frases (frase) VALUES (?)", (frase,))
    conn.commit()
    conn.close()
    response = 'Xingamento adicionado com sucesso! Seu zuero!'
    logging.info(f"Resposta para @{username}: {response}")
    bot.send_message(message.chat.id, response)

@bot.message_handler(commands=['list'])
def list_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    chat_id = message.chat.id
    if message.chat.type != 'private':
        admin_ids = [admin.user.id for admin in bot.get_chat_administrators(chat_id) if admin.status != 'creator']
        owner_id = [admin for admin in bot.get_chat_administrators(chat_id) if admin.status == 'creator'][0].user.id
        if message.from_user.id == owner_id or message.from_user.id in admin_ids:
            conn = sqlite3.connect('frases.db')
            c = conn.cursor()
            c.execute("SELECT id, frase FROM frases")
            frases = c.fetchall()
            conn.close()
            if not frases:
                response = 'Não há frases cadastradas.'
                logging.info(f"Resposta para @{username}: {response}")
                bot.send_message(message.chat.id, response)
            else:
                response = 'Xingamentos cadastrados:'
                logging.info(f"Resposta para @{username}: {response}")
                bot.send_message(message.chat.id, response)
                chunk_size = 20
                for i in range(0, len(frases), chunk_size):
                    message_text = '\n'.join([f'{frase[0]}: {frase[1]}' for frase in frases[i:i+chunk_size]])
                    logging.info(f"Resposta para @{username}: {message_text}")
                    bot.send_message(message.chat.id, message_text)
                    time.sleep(5)
        else:
            response = 'Apenas o administrador e o dono do grupo podem executar este comando'
            logging.info(f"Resposta para @{username}: {response}")
            bot.send_message(message.chat.id, response)
    else:
        response = 'Este comando não pode ser usado em chats privados'
        logging.info(f"Resposta para @{username}: {response}")
        bot.send_message(message.chat.id, response)

@bot.message_handler(commands=['remover'])
def remover_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    chat_id = message.chat.id
    user_id = message.from_user.id
    if message.chat.type != 'private':
        admin_ids = [admin.user.id for admin in bot.get_chat_administrators(chat_id) if admin.status != 'creator']
        owner_id = [admin for admin in bot.get_chat_administrators(chat_id) if admin.status == 'creator'][0].user.id
        if user_id == owner_id or user_id in admin_ids:
            frase_list = message.text.split()
            if len(frase_list) < 2:
                response = 'Insira um ID válido para remover'
                logging.info(f"Resposta para @{username}: {response}")
                bot.send_message(message.chat.id, response)
                return
            frase_id = frase_list[1]
            if not frase_id.isdigit():
                response = 'Insira um ID válido para remover, ID é um número, seu MACACO!'
                logging.info(f"Resposta para @{username}: {response}")
                bot.send_message(message.chat.id, response)
                return
            frase = frase_list[1]
            conn = sqlite3.connect('frases.db')
            c = conn.cursor()
            c.execute("DELETE FROM frases WHERE ID = ?", (frase,))
            conn.commit()
            conn.close()
            response = 'Xingamento removido com sucesso!'
            logging.info(f"Resposta para @{username}: {response}")
            bot.send_message(message.chat.id, response)
        else:
            response = 'Somente o dono do grupo e administradores podem executar este comando.'
            logging.info(f"Resposta para @{username}: {response}")
            bot.send_message(message.chat.id, response)
    else:
        response = 'Este comando não pode ser executado em conversas privadas.'
        logging.info(f"Resposta para @{username}: {response}")
        bot.send_message(message.chat.id, response)

# Handler pra responder a "boa cabelo" com "vlw barba"
@bot.message_handler(func=lambda message: message.chat.type != 'private' and
                    message.text is not None and
                    "boa cabelo" in message.text.lower())
def responder_boa_cabelo(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    response = "vlw barba"
    logging.info(f"Resposta para @{username}: {response}")
    bot.reply_to(message, response)

# Handler pra gerar imagens com xAI
def responder_imagem(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    inicio = time.time()
    prompt = message.text[len('/imagem'):].strip()
    chat_id = message.chat.id

    if not prompt:
        response = "Por favor, forneça uma descrição para a imagem. Exemplo: /imagem porco deitado na grama"
        logging.info(f"Resposta para @{username}: {response}")
        bot.reply_to(message, response)
        return

    try:
        xai_api_url = "https://api.x.ai/v1/images/generations"
        headers = {
            "Authorization": f"Bearer {XAI_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "grok-2-image",
            "prompt": prompt,
            "n": 1
        }

        response = requests.post(xai_api_url, json=payload, headers=headers)
        response.raise_for_status()

        image_url = response.json()["data"][0]["url"]

        if image_url:
            duracao = round(time.time() - inicio, 2)
            legenda = f"🖼️ Imagem gerada em {duracao} segundos"
            logging.info(f"Imagem gerada com sucesso em {duracao} segundos para prompt: '{prompt}' por @{username}")
            bot.send_photo(message.chat.id, image_url, caption=legenda, reply_to_message_id=message.message_id)
            # Salva o prompt da imagem pra contexto futuro
            last_image_prompt[chat_id] = prompt
            chat_memory[chat_id].append({"role": "assistant", "content": f"[Imagem gerada com prompt: {prompt}]"})
        else:
            response_text = "Não consegui obter a imagem gerada."
            logging.info(f"Resposta para @{username}: {response_text}")
            bot.reply_to(message, response_text)

    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response else "Unknown"
        try:
            erro_detalhes = response.json()
            mensagem_erro = erro_detalhes.get("error", "Erro desconhecido.")
            codigo_erro = erro_detalhes.get("code", "sem código")
            response_text = f"❌ Erro ao gerar imagem:\n{mensagem_erro}\n\n(código: {status_code} - {codigo_erro})"
        except Exception:
            response_text = f"❌ Erro ao chamar a API da xAI: {str(e)}"
        logging.error(f"[ERROR] HTTP para @{username}: Status: {status_code}")
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.reply_to(message, response_text)

    except Exception as e:
        error_msg = f"[ERROR] Inesperado para @{username}: {str(e)}"
        logging.error(error_msg)
        response_text = f"Ocorreu um erro: {str(e)}"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.reply_to(message, response_text)

@bot.message_handler(func=lambda message: message.text.lower().startswith('/imagem'))
def handle_imagem(message):
    responder_imagem(message)

# Inicia o bot e mantém ele rodando
try:
    logging.info("Iniciando polling do bot...")
    bot.polling()
except Exception as e:
    logging.error(f"[ERROR] Falha crítica no polling: {str(e)}")
    raise
