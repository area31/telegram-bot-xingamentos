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
sys.path.append('/home/morfetico/.local/lib/python3.14/site-packages/')
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

config_awesome = configparser.ConfigParser()
config_awesome.read('token-awesome.cfg')
AWESOME_API_KEY = config_awesome['DEFAULT']['TOKEN']
logging.debug("Token da Awesome API lido com sucesso")

# Limite de caracteres do Telegram
TELEGRAM_MAX_CHARS = 4096

# Armazenamento em memória
stored_info = {}
chat_memory = {}
last_image_prompt = {}

# Auto limpeza, combinação de tempo ocioso e limite de tamanho
CLEAN_IDLE_SECS = 15 * 60  # 15 minutos
CLEAN_MAX_MESSAGES = 20     # máximo de mensagens por chat na memória

last_activity = {}
_last_global_clean_ts = 0

# Limpa o chat_memory na inicialização
chat_memory.clear()
logging.debug("chat_memory limpo na inicialização")

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
BOT_AI = config_bot['DEFAULT'].get('BOT_AI', 'xai')
XAI_MODEL = config_bot['DEFAULT'].get('XAI_MODEL', 'grok-3-mini-fast-beta')
IMAGE_AI = config_bot['DEFAULT'].get('IMAGE_AI', 'xai')  # Nova configuração para geração de imagens
OPENAI_IMAGE_MODEL = config_bot['DEFAULT'].get('OPENAI_IMAGE_MODEL', 'dall-e-2')
OPENAI_IMAGE_SIZE = config_bot['DEFAULT'].get('OPENAI_IMAGE_SIZE', '1024x1024')
OPENAI_IMAGE_QUALITY = config_bot['DEFAULT'].get('OPENAI_IMAGE_QUALITY', None)
logging.debug(
    f"Configuração do bot: BOT_AI={BOT_AI}, XAI_MODEL={XAI_MODEL}, IMAGE_AI={IMAGE_AI}, "
    f"OPENAI_IMAGE_MODEL={OPENAI_IMAGE_MODEL}, OPENAI_IMAGE_SIZE={OPENAI_IMAGE_SIZE}, "
    f"OPENAI_IMAGE_QUALITY={OPENAI_IMAGE_QUALITY}"
)

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

def escape_md_v2(text: str) -> str:
    # Escapa caracteres reservados do MarkdownV2, exceto '*'
    return re.sub(r'([_\\[\]~`>#+\-=|{}.!])', r'\\\1', text)

def to_html(text: str) -> str:
    # Separa blocos de código para evitar interpretação de <...> como tags HTML
    code_blocks = []
    def store_code_block(match):
        code_blocks.append(match.group(0))
        return f"__CODE_BLOCK_{len(code_blocks)-1}__"

    texto = re.sub(r'```[\s\S]*?```', store_code_block, text, flags=re.DOTALL)

    # Aplica formatações HTML fora dos blocos de código
    html = re.sub(r'\*\*([^\*]+)\*\*', r'<b>\1</b>', texto)
    html = re.sub(r'_([^_]+)_', r'<i>\1</i>', html)
    html = re.sub(r'\[([^\]]+)\]\((https?://[^\s)]+)\)', r'<a href="\2">\1</a>', html)

    # Restaura blocos de código, escapando < e > para evitar interpretação como tags HTML
    for i, block in enumerate(code_blocks):
        # Remove o delimitador ``` e o tipo de linguagem (ex.: ```c)
        content = block.strip('`').split('\n', 1)
        if len(content) > 1:
            lang, code = content
            lang = lang.strip()
            code = code.strip()
        else:
            lang = ""
            code = content[0].strip()
        # Escapa < e > dentro do código
        code = code.replace('<', '&lt;').replace('>', '&gt;')
        # Restaura o bloco de código com formatação HTML
        html_block = f"<pre><code>{code}</code></pre>"
        html = html.replace(f"__CODE_BLOCK_{i}__", html_block)

    return html

def _maybe_autoclean(now=None):
    """
    Limpa contextos muito antigos e corta histórico muito grande, sem bloquear o fluxo.
    Regras, se um chat ficou ocioso por mais que CLEAN_IDLE_SECS, apaga o contexto dele.
    Se um chat passou de CLEAN_MAX_MESSAGES, mantém só o final.
    """
    global _last_global_clean_ts
    now = now or time.time()

    # roda no máximo uma varredura por minuto, pra não pesar
    if now - _last_global_clean_ts < 60:
        return
    _last_global_clean_ts = now

    # varredura dos chats
    to_delete = []
    for chat_id, msgs in list(chat_memory.items()):
        last = last_activity.get(chat_id, 0)
        if last and (now - last) > CLEAN_IDLE_SECS:
            to_delete.append(chat_id)
            continue
        if len(msgs) > CLEAN_MAX_MESSAGES:
            chat_memory[chat_id] = msgs[-CLEAN_MAX_MESSAGES:]
            logging.debug(f"chat_memory cortado para {len(chat_memory[chat_id])} mensagens no chat {chat_id}")

    for chat_id in to_delete:
        chat_memory.pop(chat_id, None)
        stored_info.pop(chat_id, None)
        last_image_prompt.pop(chat_id, None)
        last_activity.pop(chat_id, None)
        logging.debug(f"chat_memory limpo por ociosidade no chat {chat_id}")

def get_chat_history(message, reply_limit: int = 12) -> list:
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else None
    events = []

    # mensagem atual
    content_now = message.text or message.caption or "[Imagem]"
    events.append({
        "role": "user",
        "content": content_now,
        "user_id": user_id,
        "ts": getattr(message, "date", None) or time.time(),
        "mid": getattr(message, "message_id", None)
    })

    # cadeia de replies
    if message.reply_to_message:
        current = message
        while current.reply_to_message and len(events) < reply_limit * 2:
            prev = current.reply_to_message
            prev_uid = prev.from_user.id if prev.from_user else None
            role = "assistant" if prev_uid and bot.get_me().id == prev_uid else "user"
            events.append({
                "role": role,
                "content": prev.text or prev.caption or "[Imagem]",
                "user_id": prev_uid,
                "ts": getattr(prev, "date", None) or 0,
                "mid": getattr(prev, "message_id", None)
            })
            current = prev

    # memória do chat
    if chat_id in chat_memory:
        for msg in chat_memory[chat_id]:
            events.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
                "user_id": msg.get("user_id"),
                "ts": msg.get("ts", 0),
                "mid": msg.get("mid")
            })

    # deduplica priorizando message_id, senão por tripla
    seen = set()
    unique = []
    for m in events:
        key = ("mid", m.get("mid")) if m.get("mid") is not None else ("triplet", m.get("role"), m.get("content"), m.get("ts"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(m)

    # ordena por tempo e usa mid como desempate
    unique.sort(key=lambda m: (m.get("ts", 0), m.get("mid") or 0))

    # mantém só o final
    history = unique[-reply_limit:]

    # limite por tokens
    token_count = count_tokens(history)
    max_allowed_tokens = MAX_TOKENS * 0.9
    while token_count > max_allowed_tokens and history:
        history.pop(0)
        token_count = count_tokens(history)

    logging.debug(f"Histórico final obtido para chat_id {chat_id}: {len(history)} mensagens")
    return history

def get_random_frase() -> str:
    with sqlite3.connect('frases.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT frase FROM frases ORDER BY RANDOM() LIMIT 1")
        result = cursor.fetchone()
        return result[0] if result else None
    logging.debug("Frase aleatória buscada")

def update_chat_memory(message):
    """
    Atualiza a memória do chat com a mensagem recebida do usuário.
    Mantém compatibilidade com o comportamento anterior, agora com:
    1) poda pelo limite CLEAN_MAX_MESSAGES
    2) marcação de última atividade por chat
    3) varredura de limpeza leve via _maybe_autoclean
    """
    try:
        cid = message.chat.id
        uid = getattr(message.from_user, "id", None)

        # extrai conteúdo textual mesmo se vier como legenda
        text = getattr(message, "text", None)
        if text is None:
            text = getattr(message, "caption", "")
        if text is None:
            text = ""

        ts = time.time()

        # garante estrutura do chat na memória
        if cid not in chat_memory:
            chat_memory[cid] = []

        chat_memory[cid].append({
            "role": "user",
            "content": text,
            "user_id": uid,
            "ts": ts,
            "mid": getattr(message, "message_id", None),
        })

        # registra atividade recente do chat
        last_activity[cid] = ts

        # poda pelo limite configurado
        if len(chat_memory[cid]) > CLEAN_MAX_MESSAGES:
            chat_memory[cid] = chat_memory[cid][-CLEAN_MAX_MESSAGES:]
            logging.debug(f"chat_memory para chat {cid} limitado a {CLEAN_MAX_MESSAGES} mensagens")

        # limpeza leve e global, roda no máx uma vez por minuto
        _maybe_autoclean(now=ts)

    except Exception as e:
        logging.exception(f"update_chat_memory falhou, erro {e}")

def format_price(price: float) -> str:
    return f"{price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

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

#######################################
# Função de resposta do BOT
def escape_md_v2_preservando_codigo(text: str) -> str:
    # Divide o texto em partes: blocos de código (```…```) e todo o resto
    parts = re.split(r'(```[\s\S]*?```)', text, flags=re.DOTALL)
    for i, part in enumerate(parts):
        if not part.startswith('```'):
            # Escapa apenas o que NÃO está entre ```…```
            parts[i] = re.sub(r'([_\\[\]()\~`>#+\-=|{}.!])', r'\\\1', part)
    return ''.join(parts)

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

    #if message.reply_to_message and message.reply_to_message.from_user:
    #    target_username = message.reply_to_message.from_user.username or "Unknown"
    #else:
    #    target_username = username
    target_username = username

    if message.chat.type == 'private' or message.from_user.is_bot:
        response_text = tf.escape_markdown_v2("Desculpe, só respondo em grupos e não a bots!")
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta enviada para @{target_username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{target_username}: {response_text}")
        return

    if not message.text:
        response_text = tf.escape_markdown_v2("Desculpe, não posso processar mensagens sem texto! Tente enviar uma mensagem com texto.")
        tf.send_markdown(bot, message.chat.id, response_text, reply_to_message_id=message.message_id)
        logging.info(f"Resposta enviada para @{target_username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{target_username}: {response_text}")
        return

    text_lower = message.text.lower().strip()
    if len(text_lower) < 5 or text_lower in ["ok", "sim", "não", "ta", "blz", "valeu"]:
        response_text = tf.escape_markdown_v2("Beleza, mas me dá mais contexto ou pergunta algo mais específico!")
        tf.send_markdown(bot, message.chat.id, response_text, reply_to_message_id=message.message_id)
        logging.info(f"Resposta enviada para @{target_username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{target_username}: {response_text}")
        return

    current_time = time.time()
    if current_time - start_time > TIME_WINDOW:
        start_time = current_time
        request_count = 0
    if request_count >= REQUEST_LIMIT:
        response_text = tf.escape_markdown_v2("Tô de boa, mas muito requisitado agora! Tenta de novo em uns segundos.")
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta enviada para @{target_username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{target_username}: {response_text}")
        return
    request_count += 1

    chat_id = message.chat.id
    if len(text_lower) < 15 and chat_id in last_image_prompt:
        try:
            msg_curta = (
                f"Sua mensagem tá meio pequena! Você tá falando da imagem gerada com o prompt '{last_image_prompt[chat_id]}'? "
                "Eu não vejo a imagem, mas posso descrever algo, e tema ou responder algo mais específico se tu explicar melhor!"
            )
            response_text = escape_md_v2_preservando_codigo(msg_curta)
            bot.send_message(
                chat_id=message.chat.id,
                text=response_text,
                parse_mode='MarkdownV2',
                reply_to_message_id=message.message_id,
                disable_web_page_preview=True
            )
            logging.info(f"Resposta enviada para @{target_username}: {response_text[:100]}...")
            logging.debug(f"Resposta completa enviada para @{target_username}: {response_text}")
            return
        except Exception as e:
            error_msg = f"[ERROR] Falha ao responder mensagem curta após imagem para @{target_username}: {str(e)}"
            logging.error(error_msg, exc_info=True)
            response_text_plain = "Ops, deu erro ao processar sua mensagem! Tente novamente."
            response_text = escape_md_v2_preservando_codigo(response_text_plain)
            bot.send_message(
                chat_id=message.chat.id,
                text=response_text,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
            logging.info(f"Resposta de erro enviada para @{target_username}: {response_text}")
            logging.debug(f"Resposta completa de erro enviada para @{target_username}: {response_text}")
            return

    chat_history = get_chat_history(message, reply_limit=12)
    update_chat_memory(message)

    system_prompt = get_prompt()
    user_id = message.from_user.id

    if "manda" in text_lower and (r"\\" in message.text or "backslash" in text_lower):
        response_text = (
            r"Beleza, entendi! Vou usar `\` pra barras invertidas fora de blocos LaTeX, e `\` dentro de expressões matemáticas "
            r"como $$\sqrt{-1}$$. Se precisar de algo específico, explica mais!"
        )
        tf.send_markdown(bot, message.chat.id, response_text, reply_to_message_id=message.message_id)
        logging.info(f"Resposta enviada para @{target_username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{target_username}: {response_text}")
        return

    if "code latex" in text_lower and "telegram" in text_lower:
        response_text = (
            "```latex\n"
            "% Fórmula de Euler\n"
            "\\documentclass{article}\n"
            "\\usepackage{amsmath}\n\n"
            "\\begin{document}\n\n"
            "A Fórmula de Euler é $$         e^{i\\pi} + 1 = 0         $$.\n\n"
            "Em geral: $$         e^{i\\theta} = \\cos(\\theta) + i \\sin(\\theta)         $$\n\n"
            "\\end{document}\n"
            "```"
        )
        tf.send_markdown(bot, message.chat.id, response_text, reply_to_message_id=message.message_id)
        logging.info(f"Resposta enviada para @{target_username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{target_username}: {response_text}")
        return

    if any(keyword in text_lower for keyword in ["quem são", "quem é", "o que é isso", "o que são", "prompt", "imagem", "último"]) and chat_id in last_image_prompt:
        response_text = tf.escape_markdown_v2(
            f"Você tá falando do último comando /imagem? O prompt foi '{last_image_prompt[chat_id]}'. "
            "Quer que eu gere outra imagem com esse prompt ou explique algo sobre ela?"
        )
        tf.send_markdown(bot, message.chat.id, response_text, reply_to_message_id=message.message_id)
        logging.info(f"Resposta enviada para @{target_username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{target_username}: {response_text}")
        return

    if "armazene" in text_lower and ("info" in text_lower or "armazene" in text_lower.split()):
        try:
            info = message.text.split("armazene", 1)[1].replace("a info:", "").strip()
            if not info:
                response_text = tf.escape_markdown_v2("Opa, amigo! Armazenar o quê? Me dá algo pra guardar!")
                tf.send_markdown(bot, message.chat.id, response_text, reply_to_message_id=message.message_id)
                logging.info(f"Resposta enviada para @{target_username}: {response_text}")
                logging.debug(f"Resposta completa enviada para @{target_username}: {response_text}")
                return
            if user_id not in stored_info:
                stored_info[user_id] = []
            stored_info[user_id].append(info)
            response_text = tf.escape_markdown_v2("Beleza, guardei a info pra você!")
            tf.send_markdown(bot, message.chat.id, response_text, reply_to_message_id=message.message_id)
            logging.info(f"Resposta enviada para @{target_username}: {response_text}")
            logging.debug(f"Resposta completa enviada para @{target_username}: {response_text}")
            return
        except IndexError:
            response_text = tf.escape_markdown_v2("Opa, amigo! Armazenar o quê? Me dá algo pra guardar!")
            tf.send_markdown(bot, message.chat.id, response_text, reply_to_message_id=message.message_id)
            logging.info(f"Resposta enviada para @{target_username}: {response_text}")
            logging.debug(f"Resposta completa enviada para @{target_username}: {response_text}")
            return

    if "quais são as infos que te pedi pra armazenar?" in text_lower or \
       "me diga o que pedi pra armazenar" in text_lower:
        if user_id in stored_info and stored_info[user_id]:
            itens = [f"- {tf.escape_markdown_v2(info)}" for info in stored_info[user_id]]
            resposta = "\n".join([tf.bold_md("Informações armazenadas:")] + itens)
            tf.send_markdown(bot, message.chat.id, resposta, reply_to_message_id=message.message_id)
            logging.info(f"Resposta enviada para @{target_username}: {resposta}")
            logging.debug(f"Resposta completa enviada para @{target_username}: {resposta}")
        else:
            response_text = tf.escape_markdown_v2("Você ainda não me passou nenhuma info pra guardar, amigo!")
            tf.send_markdown(bot, message.chat.id, response_text, reply_to_message_id=message.message_id)
            logging.info(f"Resposta de erro enviada para @{target_username}: {response_text}")
            logging.debug(f"Resposta completa de erro enviada para @{target_username}: {response_text}")
        return

    if "limpe tudo que armazenou" in text_lower:
        clear_stored_info(user_id)
        response_text = tf.escape_markdown_v2("Feito, amigo! Tudo limpo, não guardei mais nada.")
        tf.send_markdown(bot, message.chat.id, response_text, reply_to_message_id=message.message_id)
        logging.info(f"Resposta enviada para @{target_username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{target_username}: {response_text}")
        return

    if user_id in stored_info:
        system_prompt += f"\nInformações que esse usuário me pediu pra guardar: {', '.join(stored_info[user_id])}"
    system_prompt += (
        r"\nQuando mencionar barras invertidas, use `\` para texto literal fora de LaTeX, "
        r"e `\` dentro de blocos matemáticos como $$\sqrt{-1}$$"
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
        if not answer or len(answer.strip()) < 3:
            answer = "Opa, não consegui processar direito sua instrução. Tenta explicar de novo ou pedir algo diferente!"
            logging.debug(f"Resposta padrão usada para @{username}: {answer}")

#        chat_memory[chat_id].append({"role": "assistant", "content": answer, "user_id": bot.get_me().id})
        chat_memory[chat_id].append({
            "role": "assistant",
            "content": answer,
            "user_id": bot.get_me().id,
            "ts": time.time(),
            "mid": None,  # se quiser, depois podemos salvar o message_id retornado pelo send_message
        })

        # Verifica o limite de caracteres do Telegram
        if len(answer) > TELEGRAM_MAX_CHARS:
            logging.warning(f"Resposta excede o limite de caracteres do Telegram ({len(answer)} > {TELEGRAM_MAX_CHARS}) para @{username}")
            answer = answer[:TELEGRAM_MAX_CHARS-50] + "... [Mensagem truncada devido ao limite de caracteres]"
            logging.debug(f"Resposta truncada para @{username}: {answer}")

        try:
            mensagem = escape_md_v2_preservando_codigo(answer)
            logging.debug(f"Texto escapado para MarkdownV2: {mensagem[:200]}...")
            bot.send_message(
                chat_id=message.chat.id,
                text=mensagem,
                parse_mode='MarkdownV2',
                reply_to_message_id=message.message_id,
                disable_web_page_preview=False
            )
            if message.reply_to_message:
                logging.info(f"Resposta enviada para @{target_username} (reply): {mensagem[:100]}...")
                logging.debug(f"Resposta completa enviada para @{target_username} (reply): {mensagem}")
            else:
                logging.info(f"Resposta enviada para @{target_username}: {mensagem[:100]}...")
                logging.debug(f"Resposta completa enviada para @{target_username} (MarkdownV2): {mensagem}")
        except Exception as e:
            logging.error(f"[ERROR] Falha ao enviar com MarkdownV2 para @{target_username}: {str(e)}")
            logging.debug(f"Mensagem problemática para @{target_username}: {mensagem[:200]}...")
            try:
                html_text = to_html(answer)
                bot.send_message(
                    chat_id=message.chat.id,
                    text=html_text,
                    parse_mode='HTML',
                    reply_to_message_id=message.message_id,
                    disable_web_page_preview=False
                )
                if message.reply_to_message:
                    logging.info(f"Resposta fallback enviada para @{target_username} (reply): {html_text[:100]}...")
                    logging.debug(f"Resposta completa fallback enviada para @{target_username} (reply, HTML): {html_text}")
                else:
                    logging.info(f"Resposta fallback enviada para @{target_username}: {html_text[:100]}...")
                    logging.debug(f"Resposta completa fallback enviada para @{target_username} (HTML): {html_text}")
            except Exception as e2:
                logging.error(f"[ERROR] Falha no fallback para @{target_username}: {str(e2)}")
                response_text = tf.escape_markdown_v2("Deu uma zica aqui, brother! Tenta depois!")
                tf.send_markdown(bot, message.chat.id, response_text)
                logging.info(f"Resposta de erro enviada para @{target_username}: {response_text}")
                logging.debug(f"Resposta completa de erro enviada para @{target_username}: {response_text}")

    except OpenAIError as e:
        error_msg = f"[ERROR] Erro na API da OpenAI para @{target_username}: {str(e)}"
        logging.error(error_msg)
        response_text = tf.escape_markdown_v2("Ops, minha cabeça de IA deu tilt! Tenta de novo!")
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta de erro enviada para @{target_username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{target_username}: {response_text}")
    except requests.exceptions.RequestException as e:
        error_msg = f"[ERROR] Erro na API da xAI para @{target_username}: {str(e)}"
        logging.error(error_msg)
        response_text = tf.escape_markdown_v2("Ops, deu problema com a xAI! Tenta de novo!")
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta de erro enviada para @{target_username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{target_username}: {response_text}")
    except Exception as e:
        error_msg = f"[ERROR] Inesperado para @{target_username}: {str(e)}"
        logging.error(error_msg)
        response_text = tf.escape_markdown_v2("Deu uma zica aqui, brother! Tenta depois!")
        tf.send_markdown(bot, message.chat.id, response_text)
        logging.info(f"Resposta de erro enviada para @{target_username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{target_username}: {response_text}")
#######################################
# Outros handlers permanecem inalterados
@bot.message_handler(commands=['youtube'])
def youtube_search_command(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")

    query = message.text.replace("/youtube", "", 1).strip()
    if not query:
        response_text = tf.escape_markdown_v2("Por favor, execute o /youtube com algum termo de busca")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
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
            response_text = tf.escape_markdown_v2("Não foram encontrados resultados para a sua pesquisa.")
            bot.send_message(
                chat_id=message.chat.id,
                text=response_text,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
            logging.info(f"Resposta enviada para @{username}: {response_text}")
            logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
            return

        lines = []
        for idx, item in enumerate(items, start=1):
            title = item["snippet"].get("title", "").strip()
            vid = item["id"].get("videoId", "").strip()
            url = f"https://www.youtube.com/watch?v={vid}"
            # Escapa título e texto do link
            # Escapa título, texto do link e URL (incluindo todos os pontos)
            escaped_title     = tf.escape_markdown_v2(title)
            escaped_link_text = tf.escape_markdown_v2("Link")
            escaped_url       = tf.escape_markdown_v2(url)
            lines.append(
                f"{idx}\\. **{escaped_title}** \\— "
                f"\\[{escaped_link_text}\\]\\({escaped_url}\\)"
            )

        # Escapa o texto do cabeçalho
        escaped_query = tf.escape_markdown_v2(f'Resultados do YouTube para "{query}"')
        header = f"🔎 **{escaped_query}**\\:\n"
        full_text = header + "\n".join(lines)
        # Envia a mensagem diretamente
        bot.send_message(
            chat_id=message.chat.id,
            text=full_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
        logging.info(f"Resposta enviada para @{username}: {full_text[:100]}...")
        logging.debug(f"Resposta completa enviada para @{username}: {full_text}")
    except Exception as e:
        logging.error(f"[ERROR] Handler /youtube para @{username}: {e}", exc_info=True)
        response_text = tf.escape_markdown_v2("Ops, algo deu errado no /youtube. Tente novamente mais tarde.")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")




@bot.message_handler(commands=['search'])
def search_command(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")

    query = message.text.replace("/search", "", 1).strip()
    if not query:
        response_text = tf.escape_markdown_v2("Por favor, execute o /search com algum termo de busca")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
        return

    API_KEY = open("token-search.cfg").read().strip()
    url = "https://google.serper.dev/search"

    try:
        headers = {
            "X-API-KEY": API_KEY,
            "Content-Type": "application/json"
        }

        payload = {
            "q": query
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        results = data.get("organic", [])

        if not results:
            response_text = tf.escape_markdown_v2("Não foram encontrados resultados para a sua pesquisa.")
            bot.send_message(
                chat_id=message.chat.id,
                text=response_text,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
            logging.info(f"Resposta enviada para @{username}: {response_text}")
            logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
            return

        lines = []
        for idx, item in enumerate(results[:5], start=1):
            title = item.get("title", "").strip()
            link = item.get("link", "").strip()

            escaped_title = tf.escape_markdown_v2(title)
            escaped_link_text = tf.escape_markdown_v2("Link")
            escaped_idx = tf.escape_markdown_v2(str(idx))
            escaped_link = tf.escape_markdown_v2(link)

            lines.append(f"{escaped_idx}\\. *{escaped_title}* \\- \\[{escaped_link_text}\\]\\({escaped_link}\\)")

        escaped_query = tf.escape_markdown_v2(f'Resultados da pesquisa para "{query}"')
        header = f"🔎 *{escaped_query}*\\:\n"
        full_text = header + "\n".join(lines)

        bot.send_message(
            chat_id=message.chat.id,
            text=full_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )

        logging.info(f"Resposta enviada para @{username}: {full_text[:100]}...")
        logging.debug(f"Resposta completa enviada para @{username}: {full_text}")

    except Exception as e:
        logging.error(f"[ERROR] Handler /search para @{username}: {e}", exc_info=True)
        response_text = tf.escape_markdown_v2("Ops, algo deu errado no /search. Tente novamente mais tarde.")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
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
            "/bch - Exibe a cotação do Bitcoin Cash em dólares",
            "/btc - Exibe a cotação do Bitcoin em dólares",
            "/bct - Não vou nem explicar kkkkkk",
            "/xmr - Exibe a cotação do Monero em dólares",
            "/tari - Exibe a cotação do Tari em dólares",
            "/ouro - Exibe a cotação do ouro (Tether Gold) em dólares",
            "/prata - Exibe a cotação da prata (XAG) em dólares",
            "/real - Comando desnecessário pelo óbvio, mas tente executar pra ver...",
            "/youtube - Exibe resultados de busca de vídeos no YouTube",
            "/search - Exibe resultados de busca",
            "/imagem - Gera uma imagem a partir de um texto (ex.: /imagem porco deitado na grama)",
            "/clean - Limpa da memória do Bot os chats anteriores"
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
            itens = [f"\\- {frase[0]}: {tf.escape_markdown_v2(frase[1])}" for frase in frases]
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


@bot.message_handler(commands=['bct'])
def reais_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")
    try:
        escaped_message = tf.escape_markdown_v2("Safadeeeenho....")
        response_text = f"**{escaped_message}**"
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
    except Exception as e:
        error_msg = f"[ERROR] Inesperado no handler /bct para @{username}: {str(e)}"
        logging.error(error_msg, exc_info=True)
        response_text = tf.escape_markdown_v2("Erro ao processar o comando /bct. Tente novamente!")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")


@bot.message_handler(commands=['clean'])
def clean_chat_memory(message):
    global chat_memory, stored_info, last_image_prompt
    chat_memory.clear()
    stored_info.clear()
    last_image_prompt.clear()
    bot.reply_to(message, "Memória do chat limpa com sucesso.")
    logging.debug("Memória do chat limpa manualmente pelo comando /clean")

@bot.message_handler(commands=['real'])
def reais_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")
    try:
        escaped_message = tf.escape_markdown_v2("O real não vale nada, é uma bosta essa moeda estado de merda!")
        response_text = f"**{escaped_message}**"
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
    except Exception as e:
        error_msg = f"[ERROR] Inesperado no handler /real para @{username}: {str(e)}"
        logging.error(error_msg, exc_info=True)
        response_text = tf.escape_markdown_v2("Erro ao processar o comando /real. Tente novamente!")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")

@bot.message_handler(commands=['euro'])
def euro_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")
    url = f"https://economia.awesomeapi.com.br/all/EUR-BRL?token={AWESOME_API_KEY}"

    try:
        response = requests.get(url, timeout=10)
        logging.debug(f"Resposta da API para /euro: status_code={response.status_code}")
        response.raise_for_status()
        euro_data = response.json()
        logging.debug(f"Dados da API: {euro_data}")
        valor_euro = euro_data['EUR']['bid']
        escaped_valor = tf.escape_markdown_v2(f"R$ {valor_euro}")
        response_text = f"O valor atual do euro em reais é **{escaped_valor}**"
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
    except (requests.exceptions.RequestException, KeyError, ValueError) as e:
        error_msg = f"[ERROR] Euro API para @{username}: {str(e)}"
        logging.error(error_msg, exc_info=True)
        response_text = tf.escape_markdown_v2("Erro ao consultar a cotação do euro. Tente novamente!")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")
    except Exception as e:
        error_msg = f"[ERROR] Inesperado no handler /euro para @{username}: {str(e)}"
        logging.error(error_msg, exc_info=True)
        response_text = tf.escape_markdown_v2("Erro inesperado ao consultar o euro. Tente novamente!")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")

@bot.message_handler(commands=['dolar'])
def dolar_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")
    url = f"https://economia.awesomeapi.com.br/all/USD-BRL?token={AWESOME_API_KEY}"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        dolar_data = response.json()
        valor_dolar = dolar_data['USD']['bid']
        # Escapa o valor do dólar para garantir que pontos sejam tratados
        escaped_valor = tf.escape_markdown_v2(f"R$ {valor_dolar}")
        response_text = f"O valor atual do dólar em reais é *{escaped_valor}*"
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
    except (requests.exceptions.RequestException, KeyError, ValueError) as e:
        error_msg = f"[ERROR] Dolar API para @{username}: {str(e)}"
        logging.error(error_msg)
        response_text = tf.escape_markdown_v2("Erro ao consultar a cotação do dólar. Tente novamente!")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")
    except Exception as e:
        error_msg = f"[ERROR] Inesperado no handler /dolar para @{username}: {str(e)}"
        logging.error(error_msg)
        response_text = tf.escape_markdown_v2("Erro inesperado ao consultar o dólar. Tente novamente!")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
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
        logging.debug(f"Resposta da API para /btc: status_code={response.status_code}")
        response.raise_for_status()
        data = response.json()
        logging.debug(f"Dados da API: {data}")
        if 'data' in data and 'priceUsd' in data['data']:
            price = round(float(data['data']['priceUsd']), 2)
            formatted_price = format_price(price)
            escaped_price = tf.escape_markdown_v2(f"${formatted_price}")
            response_text = f"Cotação atual do Bitcoin em dólar: **{escaped_price}**"
            bot.send_message(
                chat_id=message.chat.id,
                text=response_text,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
            logging.info(f"Resposta enviada para @{username}: {response_text}")
            logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
        else:
            error_msg = data.get('data', {}).get('message', 'Resposta inesperada da API')
            response_text = tf.escape_markdown_v2(f"Erro ao obter cotação do Bitcoin: {error_msg}")
            bot.send_message(
                chat_id=message.chat.id,
                text=response_text,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
            logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
            logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response else "Unknown"
        response_text = tf.escape_markdown_v2(f"Erro ao consultar Bitcoin: Problema na API (HTTP {status_code})")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")
    except requests.exceptions.RequestException as e:
        response_text = tf.escape_markdown_v2("Erro ao consultar Bitcoin: Falha na conexão com a API")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")
    except (KeyError, TypeError, ValueError) as e:
        response_text = tf.escape_markdown_v2("Erro ao consultar Bitcoin: Resposta inválida da API")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")
    except Exception as e:
        error_msg = f"[ERROR] Inesperado no handler /btc para @{username}: {str(e)}"
        logging.error(error_msg, exc_info=True)
        response_text = tf.escape_markdown_v2("Erro inesperado ao consultar Bitcoin. Tente novamente!")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")


@bot.message_handler(commands=['bch'])
def bch_price(message):
    username = message.from_user.username or "Unknown"
    base_url = "https://rest.coincap.io/v3"
    headers = {"Authorization": f"Bearer {COINCAP_API_KEY}"}
    assets_url = f"{base_url}/assets?apiKey={COINCAP_API_KEY}"

    try:
        # Passo 1: Verificar se o Bitcoin Cash está na lista de ativos
        response = requests.get(assets_url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        bch_data = next((asset for asset in data['data'] if asset['id'].lower() == 'bitcoin-cash'), None)
        if not bch_data:
            logging.info(f"Bitcoin Cash não está listado para @{username}")
            response_text = tf.escape_markdown_v2("Bitcoin Cash não está disponível na API da CoinCap.")
            bot.send_message(
                chat_id=message.chat.id,
                text=response_text,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
            return

        logging.info(f"Bitcoin Cash está listado para @{username}")

        # Passo 2: Consultar o preço do Bitcoin Cash
        bch_id = bch_data['id']
        url = f"{base_url}/assets/{bch_id}?apiKey={COINCAP_API_KEY}"
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        if 'data' in data and 'priceUsd' in data['data']:
            price = round(float(data['data']['priceUsd']), 2)
            formatted_price = f"{price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            escaped_price = tf.escape_markdown_v2(f"${formatted_price}")
            response_text = f"Cotação atual do Bitcoin Cash em dólar: {escaped_price}"
            bot.send_message(
                chat_id=message.chat.id,
                text=response_text,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
        else:
            logging.info(f"Bitcoin Cash está listado, mas sem dados de preço para @{username}")
            response_text = tf.escape_markdown_v2("Erro ao obter cotação do Bitcoin Cash: Dados de preço não disponíveis.")
            bot.send_message(
                chat_id=message.chat.id,
                text=response_text,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )

    except requests.exceptions.HTTPError as e:
        logging.info(f"Bitcoin Cash não está listado ou erro na API para @{username}")
        status_code = e.response.status_code if e.response else "Unknown"
        response_text = tf.escape_markdown_v2(f"Erro ao consultar Bitcoin Cash: Problema na API (HTTP {status_code})")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )

    except requests.exceptions.RequestException:
        logging.info(f"Bitcoin Cash não está listado ou falha de conexão para @{username}")
        response_text = tf.escape_markdown_v2("Erro ao consultar Bitcoin Cash: Falha na conexão com a API")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )

    except (KeyError, TypeError, ValueError):
        logging.info(f"Bitcoin Cash não está listado ou resposta inválida para @{username}")
        response_text = tf.escape_markdown_v2("Erro ao consultar Bitcoin Cash: Resposta inválida da API")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )

    except Exception:
        logging.info(f"Bitcoin Cash: erro inesperado para @{username}")
        response_text = tf.escape_markdown_v2("Erro inesperado ao consultar Bitcoin Cash. Tente novamente!")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )


@bot.message_handler(commands=['prata'])
def prata_price(message):
    username = message.from_user.username or "Unknown"

    # Lê o token do arquivo
    try:
        with open("token-apiseddotcom.cfg") as f:
            api_key = f.read().strip()
    except Exception as e:
        bot.reply_to(message, f"Erro ao ler token: {e}")
        return

    base_url = "https://metals.g.apised.com/v1/latest"
    params = {"symbols": "XAG", "base_currency": "USD"}
    headers = {"x-api-key": api_key}

    try:
        response = requests.get(base_url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get("status") == "success" and "rates" in data["data"]:
            silver_price = float(data["data"]["rates"]["XAG"])
            silver_per_gram = silver_price / 31.1035
            silver_per_kilo = silver_per_gram * 1000
            response_text = (
                f"Cotação atual da prata (XAG) em dólar:\n"
                f"${silver_price:.2f} por onça troy (31,1035 gramas)\n"
                f"${silver_per_gram:.2f} por grama\n"
                f"${silver_per_kilo:.2f} por quilo"
            )
        else:
            response_text = f"@{username}, não consegui pegar a cotação da Prata (XAG)."

    except Exception as e:
        response_text = f"@{username}, erro ao consultar API: {e}"

    bot.reply_to(message, response_text)

@bot.message_handler(commands=['ouro'])
def ouro_price(message):
    username = message.from_user.username or "Unknown"
    base_url = "https://rest.coincap.io/v3"
    headers = {"Authorization": f"Bearer {COINCAP_API_KEY}"}
    assets_url = f"{base_url}/assets?apiKey={COINCAP_API_KEY}"

    try:
        # Passo 1: Verificar se o Tether Gold está na lista de ativos
        response = requests.get(assets_url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        gold_data = next((asset for asset in data['data'] if asset['id'].lower() == 'tether-gold'), None)
        if not gold_data:
            logging.info(f"Tether Gold não está listado para @{username}")
            response_text = tf.escape_markdown_v2("Ouro (Tether Gold) não está disponível na API da CoinCap.")
            bot.send_message(
                chat_id=message.chat.id,
                text=response_text,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
            return

        logging.info(f"Tether Gold está listado para @{username}")
        
        # Passo 2: Consultar o preço do Tether Gold
        gold_id = gold_data['id']
        url = f"{base_url}/assets/{gold_id}?apiKey={COINCAP_API_KEY}"
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        if 'data' in data and 'priceUsd' in data['data']:
            price_per_oz = round(float(data['data']['priceUsd']), 2)
            price_per_gram = price_per_oz / 31.1035
            formatted_price_oz = f"{price_per_oz:.2f}"
            formatted_price_gram = f"{price_per_gram:.2f}"
            escaped_price_oz = tf.escape_markdown_v2(f"${formatted_price_oz}")
            escaped_price_gram = tf.escape_markdown_v2(f"${formatted_price_gram}")
            response_text = (
                f"Cotação atual do ouro \\(Tether Gold\\) em dólar:\n"
                f"**{escaped_price_oz}** por onça troy \\(31,1035 gramas\\)\n"
                f"**{escaped_price_gram}** por grama"
            )
            bot.send_message(
                chat_id=message.chat.id,
                text=response_text,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )

        else:
            logging.info(f"Tether Gold está listado, mas sem dados de preço para @{username}")
            response_text = tf.escape_markdown_v2("Erro ao obter cotação do ouro (Tether Gold): Dados de preço não disponíveis.")
            bot.send_message(
                chat_id=message.chat.id,
                text=response_text,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )

    except requests.exceptions.HTTPError as e:
        logging.info(f"Tether Gold não está listado ou erro na API para @{username}")
        status_code = e.response.status_code if e.response else "Unknown"
        response_text = tf.escape_markdown_v2(f"Erro ao consultar ouro (Tether Gold): Problema na API (HTTP {status_code})")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )

    except requests.exceptions.RequestException:
        logging.info(f"Tether Gold não está listado ou falha de conexão para @{username}")
        response_text = tf.escape_markdown_v2("Erro ao consultar ouro (Tether Gold): Falha na conexão com a API")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )

    except (KeyError, TypeError, ValueError):
        logging.info(f"Tether Gold não está listado ou resposta inválida para @{username}")
        response_text = tf.escape_markdown_v2("Erro ao consultar ouro (Tether Gold): Resposta inválida da API")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )

    except Exception:
        logging.info(f"Tether Gold: erro inesperado para @{username}")
        response_text = tf.escape_markdown_v2("Erro inesperado ao consultar ouro (Tether Gold). Tente novamente!")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )


@bot.message_handler(commands=['tari'])
def tari_price(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")
    try:
        # Consulta à API do CoinGecko (sem autenticação)
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": "minotari",
            "vs_currencies": "usd"
        }
        response = requests.get(url, params=params, timeout=10)
        logging.debug(f"Resposta da API CoinGecko: status_code={response.status_code}")
        response.raise_for_status()
        data = response.json()
        logging.debug(f"Dados recebidos: {data}")
        price = data.get("minotari", {}).get("usd", None)
        if price is None:
            response_text = tf.escape_markdown_v2("Tari ainda não tem cotação disponível.")
        else:
            # Formatação condicional exclusiva para Tari
            if price >= 0.01:
                # Preço ≥ 0.01: formato encurtado com 2 casas decimais (padrão BR)
                formatted = f"{price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            else:
                # Preço < 0.01: valor completo com 6 casas decimais (padrão BR)
                formatted = f"{price:.6f}".replace(".", ",")
            escaped_price = tf.escape_markdown_v2(f"${formatted}")
            response_text = f"Cotação atual do Tari em dólar: **{escaped_price}**"
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
    except requests.exceptions.RequestException as e:
        logging.error(f"[ERROR] Falha de conexão na consulta do Tari para @{username}: {str(e)}", exc_info=True)
        response_text = tf.escape_markdown_v2("Erro ao consultar Tari: Falha na conexão com a API")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
    except (KeyError, TypeError, ValueError) as e:
        logging.error(f"[ERROR] Resposta inválida ao consultar Tari para @{username}: {str(e)}", exc_info=True)
        response_text = tf.escape_markdown_v2("Erro ao consultar Tari: Resposta inválida da API")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
    except Exception as e:
        logging.error(f"[ERROR] Inesperado no handler /tari para @{username}: {str(e)}", exc_info=True)
        response_text = tf.escape_markdown_v2("Erro inesperado ao consultar Tari. Tente novamente!")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )

@bot.message_handler(commands=['xmr'])
def handle_xmr(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")
    url = f"https://rest.coincap.io/v3/assets/monero?apiKey={COINCAP_API_KEY}"

    try:
        response = requests.get(url, timeout=10)
        logging.debug(f"Resposta da API para /xmr: status_code={response.status_code}")
        response.raise_for_status()
        data = response.json()
        logging.debug(f"Dados da API: {data}")
        if 'data' in data and 'priceUsd' in data['data']:
            price = round(float(data['data']['priceUsd']), 2)
            formatted_price = format_price(price)
            escaped_price = tf.escape_markdown_v2(f"${formatted_price}")
            response_text = f"Cotação atual do Monero em dólar: **{escaped_price}**"
            bot.send_message(
                chat_id=message.chat.id,
                text=response_text,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
            logging.info(f"Resposta enviada para @{username}: {response_text}")
            logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
        else:
            error_msg = data.get('data', {}).get('message', 'Resposta inesperada da API')
            response_text = tf.escape_markdown_v2(f"Erro ao obter cotação do Monero: {error_msg}")
            bot.send_message(
                chat_id=message.chat.id,
                text=response_text,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
            logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
            logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response else "Unknown"
        response_text = tf.escape_markdown_v2(f"Erro ao consultar Monero: Problema na API (HTTP {status_code})")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")
    except requests.exceptions.RequestException as e:
        response_text = tf.escape_markdown_v2("Erro ao consultar Monero: Falha na conexão com a API")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")
    except (KeyError, TypeError, ValueError) as e:
        response_text = tf.escape_markdown_v2("Erro ao consultar Monero: Resposta inválida da API")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
        logging.info(f"Resposta de erro enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa de erro enviada para @{username}: {response_text}")
    except Exception as e:
        error_msg = f"[ERROR] Inesperado no handler /xmr para @{username}: {str(e)}"
        logging.error(error_msg, exc_info=True)
        response_text = tf.escape_markdown_v2("Erro inesperado ao consultar Monero. Tente novamente!")
        bot.send_message(
            chat_id=message.chat.id,
            text=response_text,
            parse_mode='MarkdownV2',
            disable_web_page_preview=True
        )
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
        response = escape_md_v2('Use /add e insira o xingamento')
        try:
            bot.send_message(
                chat_id=message.chat.id,
                text=response,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
            logging.info(f"Resposta enviada para @{username}: {response}")
            logging.debug(f"Resposta completa enviada para @{username}: {response}")
        except Exception as e:
            logging.error(f"[ERROR] Falha ao enviar mensagem MarkdownV2 para @{username}: {str(e)}")
            bot.send_message(
                chat_id=message.chat.id,
                text='Use /add e insira o xingamento',
                disable_web_page_preview=True
            )
            logging.info(f"Resposta texto puro enviada para @{username}: Use /add e insira o xingamento")
        return

    if len(frase) > 150:
        response = escape_md_v2('Xingamento muito longo, por favor use até 150 caracteres')
        try:
            bot.send_message(
                chat_id=message.chat.id,
                text=response,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
            logging.info(f"Resposta enviada para @{username}: {response}")
            logging.debug(f"Resposta completa enviada para @{username}: {response}")
        except Exception as e:
            logging.error(f"[ERROR] Falha ao enviar mensagem MarkdownV2 para @{username}: {str(e)}")
            bot.send_message(
                chat_id=message.chat.id,
                text='Xingamento muito longo, por favor use até 150 caracteres',
                disable_web_page_preview=True
            )
            logging.info(f"Resposta texto puro enviada para @{username}: Xingamento muito longo")
        return

    try:
        insert_frase(frase)
        response = escape_md_v2('Xingamento adicionado com sucesso seu zuero')
        try:
            bot.send_message(
                chat_id=message.chat.id,
                text=response,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
            logging.info(f"Resposta enviada para @{username}: {response}")
            logging.debug(f"Resposta completa enviada para @{username}: {response}")
        except Exception as e:
            logging.error(f"[ERROR] Falha ao enviar mensagem MarkdownV2 para @{username}: {str(e)}")
            bot.send_message(
                chat_id=message.chat.id,
                text='Xingamento adicionado com sucesso seu zuero',
                disable_web_page_preview=True
            )
            logging.info(f"Resposta texto puro enviada para @{username}: Xingamento adicionado com sucesso")
    except sqlite3.Error as e:
        logging.error(f"[ERROR] Falha ao inserir frase no banco de dados para @{username}: {str(e)}")
        response = escape_md_v2('Erro ao adicionar o xingamento, tente novamente')
        try:
            bot.send_message(
                chat_id=message.chat.id,
                text=response,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
            logging.info(f"Resposta enviada para @{username}: {response}")
            logging.debug(f"Resposta completa enviada para @{username}: {response}")
        except Exception as send_error:
            logging.error(f"[ERROR] Falha ao enviar mensagem MarkdownV2 para @{username}: {str(send_error)}")
            bot.send_message(
                chat_id=message.chat.id,
                text='Erro ao adicionar o xingamento, tente novamente',
                disable_web_page_preview=True
            )
            logging.info(f"Resposta texto puro enviada para @{username}: Erro ao adicionar o xingamento")


@bot.message_handler(commands=['xinga'])
def random_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")

    try:
        conn = sqlite3.connect('frases.db')
        c = conn.cursor()
        c.execute("SELECT frase FROM frases")
        frases = c.fetchall()
        conn.close()

        if not frases:
            response = escape_md_v2('Não há frases cadastradas')
            try:
                bot.send_message(
                    chat_id=message.chat.id,
                    text=response,
                    parse_mode='MarkdownV2',
                    disable_web_page_preview=True
                )
                logging.info(f"Resposta enviada para @{username}: {response}")
                logging.debug(f"Resposta completa enviada para @{username}: {response}")
            except Exception as e:
                logging.error(f"[ERROR] Falha ao enviar mensagem MarkdownV2 para @{username}: {str(e)}")
                bot.send_message(
                    chat_id=message.chat.id,
                    text='Não há frases cadastradas',
                    disable_web_page_preview=True
                )
                logging.info(f"Resposta texto puro enviada para @{username}: Não há frases cadastradas")
            return

        frase_escolhida = escape_md_v2(random.choice(frases)[0])

        if message.reply_to_message and hasattr(message.reply_to_message, 'from_user'):
            target_user = message.reply_to_message.from_user.username or "Unknown"
            try:
                bot.send_message(
                    chat_id=message.chat.id,
                    text=frase_escolhida,
                    parse_mode='MarkdownV2',
                    reply_to_message_id=message.reply_to_message.message_id,
                    disable_web_page_preview=True
                )
                logging.info(f"Resposta enviada para @{target_user} (reply): {frase_escolhida}")
                logging.debug(f"Resposta completa enviada para @{target_user} (reply): {frase_escolhida}")
            except Exception as e:
                logging.error(f"[ERROR] Falha ao enviar mensagem MarkdownV2 para @{target_user}: {str(e)}", exc_info=True)
                bot.send_message(
                    chat_id=message.chat.id,
                    text=frase_escolhida.replace('\\', ''),
                    reply_to_message_id=message.reply_to_message.message_id,
                    disable_web_page_preview=True
                )
                logging.info(f"Resposta texto puro enviada para @{target_user} (reply): {frase_escolhida.replace('\\', '')}")
        else:
            command_parts = message.text.split(maxsplit=2)
            if len(command_parts) > 1 and command_parts[1].startswith('@'):
                target_user = command_parts[1].lstrip('@')
                response = f"{escape_md_v2(command_parts[1])} {frase_escolhida}"
                try:
                    bot.send_message(
                        chat_id=message.chat.id,
                        text=response,
                        parse_mode='MarkdownV2',
                        disable_web_page_preview=True
                    )
                    logging.info(f"Resposta enviada para @{target_user}: {response}")
                    logging.debug(f"Resposta completa enviada para @{target_user}: {response}")
                except Exception as e:
                    logging.error(f"[ERROR] Falha ao enviar mensagem MarkdownV2 para @{target_user}: {str(e)}", exc_info=True)
                    bot.send_message(
                        chat_id=message.chat.id,
                        text=f"{command_parts[1]} {frase_escolhida.replace('\\', '')}",
                        disable_web_page_preview=True
                    )
                    logging.info(f"Resposta texto puro enviada para @{target_user}: {command_parts[1]} {frase_escolhida.replace('\\', '')}")
            else:
                target_user = username
                try:
                    bot.send_message(
                        chat_id=message.chat.id,
                        text=frase_escolhida,
                        parse_mode='MarkdownV2',
                        disable_web_page_preview=True
                    )
                    logging.info(f"Resposta enviada para @{target_user}: {frase_escolhida}")
                    logging.debug(f"Resposta completa enviada para @{target_user}: {frase_escolhida}")
                except Exception as e:
                    logging.error(f"[ERROR] Falha ao enviar mensagem MarkdownV2 para @{target_user}: {str(e)}", exc_info=True)
                    bot.send_message(
                        chat_id=message.chat.id,
                        text=frase_escolhida.replace('\\', ''),
                        disable_web_page_preview=True
                    )
                    logging.info(f"Resposta texto puro enviada para @{target_user}: {frase_escolhida.replace('\\', '')}")

    except sqlite3.Error as e:
        logging.error(f"[ERROR] Falha ao acessar banco de dados para @{username}: {str(e)}", exc_info=True)
        response = escape_md_v2('Erro ao buscar xingamentos, tente novamente')
        try:
            bot.send_message(
                chat_id=message.chat.id,
                text=response,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
            logging.info(f"Resposta enviada para @{username}: {response}")
            logging.debug(f"Resposta completa enviada para @{username}: {response}")
        except Exception as send_error:
            logging.error(f"[ERROR] Falha ao enviar mensagem MarkdownV2 para @{username}: {str(send_error)}", exc_info=True)
            bot.send_message(
                chat_id=message.chat.id,
                text='Erro ao buscar xingamentos, tente novamente',
                disable_web_page_preview=True
            )
            logging.info(f"Resposta texto puro enviada para @{username}: Erro ao buscar xingamentos")

@bot.message_handler(commands=['remover'])
def remover_message(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")

    chat_id = message.chat.id
    user_id = message.from_user.id

    try:
        if message.chat.type == 'private':
            response = escape_md_v2('Este comando não pode ser executado em conversas privadas')
            try:
                bot.send_message(
                    chat_id=message.chat.id,
                    text=response,
                    parse_mode='MarkdownV2',
                    disable_web_page_preview=True
                )
                logging.info(f"Resposta enviada para @{username}: {response}")
                logging.debug(f"Resposta completa enviada para @{username}: {response}")
            except Exception as e:
                logging.error(f"[ERROR] Falha ao enviar mensagem MarkdownV2 para @{username}: {str(e)}")
                bot.send_message(
                    chat_id=message.chat.id,
                    text='Este comando não pode ser executado em conversas privadas',
                    disable_web_page_preview=True
                )
                logging.info(f"Resposta texto puro enviada para @{username}: Este comando não pode ser executado em conversas privadas")
            return

        admin_ids = [admin.user.id for admin in bot.get_chat_administrators(chat_id) if admin.status != 'creator']
        owner_id = [admin for admin in bot.get_chat_administrators(chat_id) if admin.status == 'creator'][0].user.id
        if user_id != owner_id and user_id not in admin_ids:
            response = escape_md_v2('Somente o dono do grupo e administradores podem executar este comando')
            try:
                bot.send_message(
                    chat_id=message.chat.id,
                    text=response,
                    parse_mode='MarkdownV2',
                    disable_web_page_preview=True
                )
                logging.info(f"Resposta enviada para @{username}: {response}")
                logging.debug(f"Resposta completa enviada para @{username}: {response}")
            except Exception as e:
                logging.error(f"[ERROR] Falha ao enviar mensagem MarkdownV2 para @{username}: {str(e)}")
                bot.send_message(
                    chat_id=message.chat.id,
                    text='Somente o dono do grupo e administradores podem executar este comando',
                    disable_web_page_preview=True
                )
                logging.info(f"Resposta texto puro enviada para @{username}: Somente o dono do grupo e administradores podem executar este comando")
            return

        frase_list = message.text.split()
        if len(frase_list) < 2:
            response = escape_md_v2('Insira um ID válido para remover')
            try:
                bot.send_message(
                    chat_id=message.chat.id,
                    text=response,
                    parse_mode='MarkdownV2',
                    disable_web_page_preview=True
                )
                logging.info(f"Resposta enviada para @{username}: {response}")
                logging.debug(f"Resposta completa enviada para @{username}: {response}")
            except Exception as e:
                logging.error(f"[ERROR] Falha ao enviar mensagem MarkdownV2 para @{username}: {str(e)}")
                bot.send_message(
                    chat_id=message.chat.id,
                    text='Insira um ID válido para remover',
                    disable_web_page_preview=True
                )
                logging.info(f"Resposta texto puro enviada para @{username}: Insira um ID válido para remover")
            return

        frase_id = frase_list[1]
        if not frase_id.isdigit():
            response = escape_md_v2('Insira um ID válido para remover, ID é um número seu MACACO')
            try:
                bot.send_message(
                    chat_id=message.chat.id,
                    text=response,
                    parse_mode='MarkdownV2',
                    disable_web_page_preview=True
                )
                logging.info(f"Resposta enviada para @{username}: {response}")
                logging.debug(f"Resposta completa enviada para @{username}: {response}")
            except Exception as e:
                logging.error(f"[ERROR] Falha ao enviar mensagem MarkdownV2 para @{username}: {str(e)}")
                bot.send_message(
                    chat_id=message.chat.id,
                    text='Insira um ID válido para remover, ID é um número seu MACACO',
                    disable_web_page_preview=True
                )
                logging.info(f"Resposta texto puro enviada para @{username}: Insira um ID válido para remover")
            return

        conn = sqlite3.connect('frases.db')
        c = conn.cursor()
        c.execute("DELETE FROM frases WHERE ID = ?", (frase_id,))
        conn.commit()
        conn.close()
        response = escape_md_v2('Xingamento removido com sucesso')
        try:
            bot.send_message(
                chat_id=message.chat.id,
                text=response,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
            logging.info(f"Resposta enviada para @{username}: {response}")
            logging.debug(f"Resposta completa enviada para @{username}: {response}")
        except Exception as e:
            logging.error(f"[ERROR] Falha ao enviar mensagem MarkdownV2 para @{username}: {str(e)}")
            bot.send_message(
                chat_id=message.chat.id,
                text='Xingamento removido com sucesso',
                disable_web_page_preview=True
            )
            logging.info(f"Resposta texto puro enviada para @{username}: Xingamento removido com sucesso")

    except Exception as e:
        logging.error(f"[ERROR] Falha no handler /remover para @{username}: {str(e)}")
        response = escape_md_v2('Erro inesperado ao executar o comando /remover, tente novamente')
        try:
            bot.send_message(
                chat_id=message.chat.id,
                text=response,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
            logging.info(f"Resposta enviada para @{username}: {response}")
            logging.debug(f"Resposta completa enviada para @{username}: {response}")
        except Exception as send_error:
            logging.error(f"[ERROR] Falha ao enviar mensagem MarkdownV2 para @{username}: {str(send_error)}")
            bot.send_message(
                chat_id=message.chat.id,
                text='Erro inesperado ao executar o comando /remover, tente novamente',
                disable_web_page_preview=True
            )
            logging.info(f"Resposta texto puro enviada para @{username}: Erro inesperado ao executar o comando /remover")


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


# Cache em memória para imagens
image_cache = {}

def imagem_advanced(prompt: str, model_priority=None, size=None, retries: int = 3):
    """
    Gera imagem usando somente o modelo definido no CFG.
    Respeita OPENAI_IMAGE_MODEL, OPENAI_IMAGE_SIZE, OPENAI_IMAGE_QUALITY.
    Não tenta modelos alternativos.
    Usa cache em memória.
    Retorna URL para DALL·E.
    Retorna BYTES (PNG) para gpt-image-*.
    """
    import time
    import os
    import base64
    from openai import OpenAI

    model = OPENAI_IMAGE_MODEL

    if model == "dall-e-2":
        allowed_sizes = {"256x256", "512x512", "1024x1024"}
        eff_size = size if size in allowed_sizes else (OPENAI_IMAGE_SIZE if OPENAI_IMAGE_SIZE in allowed_sizes else "512x512")
        eff_quality = None
        want_url = True

    elif model == "dall-e-3":
        allowed_sizes = {"1024x1024", "1024x1792", "1792x1024"}
        eff_size = size if size in allowed_sizes else (OPENAI_IMAGE_SIZE if OPENAI_IMAGE_SIZE in allowed_sizes else "1024x1024")
        eff_quality = OPENAI_IMAGE_QUALITY if OPENAI_IMAGE_QUALITY in ("standard", "hd") else "standard"
        want_url = True

    elif model in ("gpt-image-1", "gpt-image-0721-mini-alpha"):
        allowed_sizes = {"1024x1024", "1024x1536", "1536x1024", "auto"}
        eff_size = size if size in allowed_sizes else (OPENAI_IMAGE_SIZE if OPENAI_IMAGE_SIZE in allowed_sizes else "1024x1024")
        eff_quality = OPENAI_IMAGE_QUALITY if OPENAI_IMAGE_QUALITY in ("low", "medium", "high") else None
        want_url = False

    else:
        raise Exception(f"Modelo de imagem inválido no cfg: {model}")

    cache_key = hash((prompt, model, eff_size, eff_quality))
    if cache_key in image_cache:
        logging.debug(f"Imagem encontrada no cache para prompt: {prompt}")
        return image_cache[cache_key]

    if OPENAI_API_KEY and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY

    client = OpenAI(api_key=OPENAI_API_KEY)

    kwargs = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": eff_size,
    }
    if eff_quality is not None:
        kwargs["quality"] = eff_quality
    if want_url:
        kwargs["response_format"] = "url"

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            logging.debug(f"[IMAGE] tentando gerar com model={model} size={kwargs.get('size')} quality={kwargs.get('quality')} attempt={attempt}")
            resp = client.images.generate(**kwargs)

            if want_url:
                image_url = resp.data[0].url
                image_cache[cache_key] = image_url
                logging.info(f"[IMAGE] sucesso model={model} size={kwargs.get('size')} quality={kwargs.get('quality')}")
                return image_url

            b64 = resp.data[0].b64_json
            img_bytes = base64.b64decode(b64)
            image_cache[cache_key] = img_bytes
            logging.info(f"[IMAGE] sucesso model={model} size={kwargs.get('size')} quality={kwargs.get('quality')}")
            return img_bytes

        except Exception as e:
            err_text = str(e)
            last_err = e
            logging.warning(f"Tentativa {attempt} falhou com modelo {model}: {err_text}")

            if "billing_hard_limit_reached" in err_text or "Billing hard limit has been reached" in err_text:
                raise Exception("Limite mensal atingido no painel da OpenAI. Aumente o Organization budget ou Project budget em Billing depois Limits.")

            if "Unknown parameter: 'response_format'" in err_text:
                kwargs.pop("response_format", None)
                continue

            if "Invalid value: 'quality'" in err_text and model == "dall-e-3":
                kwargs["quality"] = "standard"
                continue

            if "Invalid value: 'size'" in err_text:
                if model == "dall-e-2":
                    kwargs["size"] = "512x512"
                elif model == "dall-e-3":
                    kwargs["size"] = "1024x1024"
                else:
                    kwargs["size"] = "1024x1024"
                continue

            time.sleep(min(2 ** (attempt - 1), 4))
            continue

    raise Exception(f"Falha ao gerar imagem com o modelo configurado {model}: {last_err if last_err else 'erro desconhecido'}")



@bot.message_handler(commands=['imagem'])
def imagem_command(message):
    username = message.from_user.username or "Unknown"
    prompt = message.text.replace("/imagem", "", 1).strip()
    chat_id = message.chat.id
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    logging.debug(f"Pergunta completa de @{username}: {message.text}")

    if not prompt:
        response_text = tf.escape_html(
            "Por favor, forneça uma descrição para a imagem.\n"
            "Exemplo: /imagem um copo de leite em uma mesa"
        )
        tf.send_html(bot, message.chat.id, response_text)
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
        return

    try:
        if IMAGE_AI.lower() == "xai":
            wait_message = "Sua imagem pode levar até 20 segundos para ser gerada. Aguarde, estou processando com xAI..."
        else:
            wait_message = "Sua imagem pode levar até 2 minutos para ser gerada. Aguarde, estou processando com OpenAI..."
        tf.send_html(bot, message.chat.id, tf.escape_html(wait_message))
        logging.info(f"Mensagem de espera enviada para @{username}: {wait_message}")
    except Exception as e:
        logging.error(f"[ERROR] Falha ao enviar mensagem de espera para @{username}: {str(e)}")

    if len(prompt) < 15 or len(prompt.split()) < 3:
        response_text = tf.escape_html(
            "O prompt é muito curto ou vago. Forneça uma descrição mais detalhada com pelo menos 3 palavras.\n"
            "Exemplo: /imagem um copo de leite em uma mesa"
        )
        tf.send_html(bot, message.chat.id, response_text)
        logging.info(f"Resposta enviada para @{username}: {response_text}")
        logging.debug(f"Resposta completa enviada para @{username}: {response_text}")
        return

    update_chat_memory(message)

    try:
        start_time = time.time()
        image_url = None
        used_model = None

        if IMAGE_AI.lower() == "openai":
            used_model = OPENAI_IMAGE_MODEL
            image_url = imagem_advanced(prompt, model_priority=[OPENAI_IMAGE_MODEL], retries=3)

        elif IMAGE_AI.lower() == "xai":
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
            used_model = payload["model"]
            try:
                resp = requests.post(API_URL, json=payload, headers=headers, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                image_url = data["data"][0]["url"]
                logging.debug(f"Imagem gerada pela xAI para @{username}: {image_url}")
            except requests.exceptions.RequestException as e:
                raise Exception(f"requests.RequestException: {str(e)}")

        else:
            raise ValueError(f"Configuração inválida para IMAGE_AI: {IMAGE_AI}. Use 'openai' ou 'xai'.")

        time_taken = round(time.time() - start_time, 2)

        try:
            caption = (
                f"🖼️ {tf.italic(tf.escape_html(f'Prompt: {prompt}'))}\n"
                f"{tf.italic(tf.escape_html(f'Modelo: {used_model}'))}\n"
                f"{tf.italic(tf.escape_html(f'Gerada com {IMAGE_AI.upper()} em {time_taken} segundos'))}"
            )

            if used_model in ("dall-e-2", "dall-e-3"):
                sent_message = bot.send_photo(
                    chat_id=message.chat.id,
                    photo=image_url,
                    caption=caption,
                    parse_mode="HTML",
                    reply_to_message_id=message.message_id
                )

            else:
                import os
                import random
                import base64

                base_dir = os.getcwd()
                try:
                    base_dir = os.path.dirname(os.path.abspath(__file__))
                except Exception:
                    pass

                tmp_dir = os.path.join(base_dir, "tmp")
                os.makedirs(tmp_dir, exist_ok=True)

                img_bytes = None

                if isinstance(image_url, (bytes, bytearray)):
                    img_bytes = bytes(image_url)

                elif isinstance(image_url, str) and image_url.startswith("data:image/"):
                    try:
                        b64_part = image_url.split(",", 1)[1]
                        img_bytes = base64.b64decode(b64_part)
                    except Exception:
                        raise Exception("Data URL inválida, não consegui decodificar base64.")

                else:
                    raise Exception("Modelo não retornou URL curta e também não retornou bytes nem data URL base64.")

                fname = f"img_{int(time.time())}_{random.randint(1000,9999)}.png"
                fpath = os.path.join(tmp_dir, fname)

                with open(fpath, "wb") as f:
                    f.write(img_bytes)

                try:
                    files = []
                    for x in os.listdir(tmp_dir):
                        p = os.path.join(tmp_dir, x)
                        if os.path.isfile(p) and x.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                            files.append(p)

                    files.sort(key=lambda p: os.path.getmtime(p))
                    excess = len(files) - 50
                    if excess > 0:
                        for old in files[:excess]:
                            try:
                                os.remove(old)
                            except Exception:
                                pass
                except Exception:
                    pass

                with open(fpath, "rb") as photo_fp:
                    sent_message = bot.send_photo(
                        chat_id=message.chat.id,
                        photo=photo_fp,
                        caption=caption,
                        parse_mode="HTML",
                        reply_to_message_id=message.message_id
                    )

            update_chat_memory(sent_message)
            last_image_prompt[chat_id] = prompt
            logging.info(f"Imagem enviada para @{username} prompt={prompt} modelo={used_model} tempo={time_taken}s AI={IMAGE_AI}")
            logging.debug(f"Imagem completa enviada para @{username} caption={caption}")

        except Exception as e:
            error_detail = f"Erro ao enviar a imagem: {str(e)}"
            if isinstance(e, requests.exceptions.RequestException):
                error_detail = f"requests.RequestException: {str(e)}"
            logging.error(f"[ERROR] Falha ao enviar imagem para @{username}: {error_detail}", exc_info=True)
            tf.send_html(
                bot,
                message.chat.id,
                tf.escape_html(f"❌ Erro ao enviar a imagem. Motivo: {error_detail}. Tente novamente mais tarde.")
            )
            logging.info(f"Resposta de erro enviada para @{username}: {error_detail}")

    except Exception as e:
        error_detail = str(e)
        logging.error(f"[ERROR] Falha ao gerar imagem para @{username}: {error_detail}", exc_info=True)
        tf.send_html(bot, message.chat.id, tf.escape_html(f"❌ Não consegui gerar a imagem. Motivo: {error_detail}"))
        logging.info(f"Resposta de erro enviada para @{username}: {error_detail}")

# Inicia o bot
try:
    logging.info("Iniciando polling do bot...")
    bot.polling(none_stop=True, interval=0, timeout=20)
except Exception as e:
    logging.error(f"[ERROR] Falha crítica no polling: {str(e)}", exc_info=True)
    time.sleep(5)  # Aguarda antes de tentar reiniciar
    logging.info("Tentando reiniciar o polling...")
    bot.polling(none_stop=True, interval=0, timeout=20)
