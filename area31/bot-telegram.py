import configparser
import os
import time
import random
import sqlite3
import requests
import sys
from typing import Tuple, Optional
sys.path.append('/home/morfetico/.local/lib/python3.12/site-packages/')
import telebot
import shutil
import openai
from openai import OpenAIError
import logging
import urllib.parse

start_time = time.time()
request_count = 0

# Configuração do log
logging.basicConfig(
    filename='/home/morfetico/alta-linguagem/bot-telegram.log',
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    filemode='a'
)
logging.info("Bot iniciado.")

# Rate limit
REQUEST_LIMIT = 40
TIME_WINDOW = 60
request_count = 0
start_time = time.time()

# Configuração do Telegram
config_telegram = configparser.ConfigParser()
config_telegram.read('token-telegram.cfg')
TOKEN = config_telegram['DEFAULT']['TOKEN']
bot = telebot.TeleBot(TOKEN)

# Configuração da CoinCap API v3
config_coincap = configparser.ConfigParser()
config_coincap.read('token-coincap.cfg')
COINCAP_API_KEY = config_coincap['DEFAULT']['TOKEN']

# Limite de caracteres do Telegram
TELEGRAM_MAX_CHARS = 4096

# Ajustes de formatação do Telegram
def escape_markdown_v2(text):
    """
    Escapa caracteres reservados para Telegram MarkdownV2, preservando sequências de escape em blocos de código.
    """
    reserved_chars = "_[]()~>#+-=|{}!."
    result = ""
    i = 0
    length = len(text)
    code_block_open = False
    code_block_content = ""

    while i < length:
        if i + 2 < length and text[i:i+3] == "```":
            if not code_block_open:
                code_block_open = True
                i += 3
                lang = ""
                while i < length and text[i] != "\n":
                    lang += text[i]
                    i += 1
                if i < length:
                    i += 1
                result += "```" + lang + "\n"
                while i < length:
                    if i + 2 < length and text[i:i+3] == "```":
                        i += 3
                        code_block_open = False
                        result += code_block_content.rstrip() + "```"
                        code_block_content = ""
                        break
                    if text[i] == "\\":
                        code_block_content += "\\\\"
                    else:
                        code_block_content += text[i]
                    i += 1
            else:
                i += 3
        elif i + 1 < length and text[i:i+2] == "**":
            result += "*" if not code_block_open else "**"
            i += 2
            while i < length and text[i:i+2] != "**":
                if text[i] in reserved_chars and not code_block_open:
                    result += "\\" + text[i]
                elif code_block_open:
                    code_block_content += text[i]
                else:
                    result += text[i]
                i += 1
            if i + 1 < length:
                result += "*" if not code_block_open else "**"
                i += 2
        elif text[i] == "`":
            result += "`" if not code_block_open else "`"
            i += 1
            while i < length and text[i] != "`":
                if code_block_open:
                    code_block_content += text[i]
                else:
                    result += text[i]
                i += 1
            if i < length:
                result += "`" if not code_block_open else "`"
                i += 1
        elif text[i] == "*":
            result += "*" if not code_block_open else "*"
            i += 1
            while i < length and text[i] != "*":
                if text[i] in reserved_chars and not code_block_open:
                    result += "\\" + text[i]
                elif code_block_open:
                    code_block_content += text[i]
                else:
                    result += text[i]
                i += 1
            if i < length:
                result += "*" if not code_block_open else "*"
                i += 1
        elif text[i] == ">":
            if not code_block_open and (i == 0 or text[i-1] == "\n"):
                result += ">"
            elif code_block_open:
                code_block_content += ">"
            else:
                result += "\\>"
            i += 1
        else:
            if code_block_open:
                code_block_content += text[i]
            elif text[i] in reserved_chars:
                result += "\\" + text[i]
            else:
                result += text[i]
            i += 1

    if code_block_open:
        result += code_block_content.rstrip() + "```"

    if not result:
        result = "Erro ao processar formatação, tente novamente."

    if len(result) > TELEGRAM_MAX_CHARS:
        result = result[:TELEGRAM_MAX_CHARS - 3] + "..."
        if code_block_open and not result.endswith("```"):
            result += "```"
    return result

# Armazenamento em memória
stored_info = {}
chat_memory = {}

def create_table():
    with sqlite3.connect('frases.db') as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS frases (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            frase TEXT NOT NULL
        );
        """)

def insert_frase(frase: str):
    with sqlite3.connect('frases.db') as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO frases (frase) VALUES (?)", (frase,))

def get_random_frase() -> str:
    with sqlite3.connect('frases.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT frase FROM frases ORDER BY RANDOM() LIMIT 1")
        result = cursor.fetchone()
        return result[0] if result else None

create_table()

# Configuração da OpenAI
config_openai = configparser.ConfigParser()
config_openai.read('token-openai.cfg')
OPENAI_API_KEY = config_openai['DEFAULT']['API_KEY']

# Configuração da xAI
config_xai = configparser.ConfigParser()
config_xai.read('token-xai.cfg')
XAI_API_KEY = config_xai['DEFAULT']['API_KEY']

# Configuração do Bot (OpenAI ou xAI)
config_bot = configparser.ConfigParser()
config_bot.read('bot-telegram.cfg')
BOT_AI = config_bot['DEFAULT'].get('BOT_AI', 'openai')  # Default para OpenAI
XAI_MODEL = config_bot['DEFAULT'].get('XAI_MODEL', 'grok-2-latest')

# Parâmetros gerais
MAX_TOKENS = 1000
TEMPERATURE = 0.8
OPENAI_MODEL = "gpt-4"

def count_tokens(messages):
    total_tokens = 0
    for msg in messages:
        total_tokens += len(msg["content"].split()) + 10
    return total_tokens

def get_prompt() -> str:
    try:
        with open('prompt.cfg', 'r', encoding='utf-8') as arquivo:
            base_prompt = arquivo.read().strip()
    except FileNotFoundError:
        base_prompt = ("Você é um assistente útil e espirituoso em um bot do Telegram. "
                       "Responda de forma natural, como um amigo conversando, mantendo o contexto da conversa. "
                       "Quando o usuário pedir para 'armazenar a info', guarde a informação em uma lista associada ao ID do usuário. "
                       "Quando perguntado 'quais são as infos que te pedi pra armazenar?', responda com a lista de informações armazenadas.")
    return f"{base_prompt}\n\nSua resposta deve ter no máximo 4000 caracteres para caber no limite do Telegram (4096 caracteres, incluindo formatação). Se necessário, resuma ou ajuste o conteúdo para não ultrapassar esse limite."

# Xingamentos
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

#    if message.reply_to_message and hasattr(message.reply_to_message, 'from_user'):
#        response = frase_escolhida
#        logging.info(f"Resposta para @{username} (reply): {response}")
#        bot.reply_to(message.reply_to_message, response)
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

# ChatGPT/xAI
@bot.message_handler(func=lambda message: message.text is not None and
                    message.from_user.id != bot.get_me().id and
                    (bot.get_me().username in message.text or
                     (message.reply_to_message is not None and
                      message.reply_to_message.from_user.id == bot.get_me().id)))
def responder(message):
    global start_time, request_count
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")

    if message.chat.type == 'private' or message.from_user.is_bot:
        response_text = "Desculpe, só respondo em grupos e não a bots!"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
        return

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

    update_chat_memory(message)
    chat_history = get_chat_history(message, reply_limit=4)
    system_prompt = get_prompt()
    user_id = message.from_user.id

    text_lower = message.text.lower()

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

    if "quais são as infos que te pedi pra armazenar?" in text_lower or \
       "me diga o que pedi pra armazenar" in text_lower:
        if user_id in stored_info and stored_info[user_id]:
            resposta = "Olha só o que você me pediu pra guardar:\n" + "\n".join(stored_info[user_id])
        else:
            resposta = "Você ainda não me passou nenhuma info pra guardar, amigo!"
        logging.info(f"Resposta para @{username}: {resposta}")
        bot.reply_to(message, escape_markdown_v2(resposta), parse_mode='MarkdownV2')
        return

    if "limpe tudo que armazenou" in text_lower:
        clear_stored_info(user_id)
        response_text = "Feito, amigo! Tudo limpo, não guardei mais nada."
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.reply_to(message, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
        return

    if user_id in stored_info:
        system_prompt += f"\nInformações que esse usuário me pediu pra guardar: {', '.join(stored_info[user_id])}"

    messages = [{"role": "system", "content": system_prompt}] + chat_history

    try:
        start_time = time.time()

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
        else:
            raise ValueError(f"Configuração inválida para BOT_AI: {BOT_AI}. Use 'openai' ou 'xai'.")

        response_time = time.time() - start_time
        if not answer or len(answer) < 3:
            answer = "Poxa, me deu um branco agora... deixa eu pensar melhor!"
        logging.info(f"Resposta gerada em {response_time:.2f}s usando {BOT_AI} para @{username}: {answer}")

        chat_memory[message.chat.id].append({"role": "assistant", "content": answer})
        answer_escaped = escape_markdown_v2(answer)

        if message.reply_to_message:
            bot.reply_to(message, answer_escaped, parse_mode='MarkdownV2')
        else:
            bot.send_message(message.chat.id, answer_escaped, parse_mode='MarkdownV2')

    except OpenAIError as e:
        error_msg = f"[ERROR] Erro na API da OpenAI: {str(e)}"
        logging.error(error_msg)
        response_text = "Ops, minha cabeça de IA deu tilt! Tenta de novo!"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
    except requests.exceptions.RequestException as e:
        error_msg = f"[ERROR] Erro na API da xAI: {str(e)}"
        logging.error(error_msg)
        response_text = "Ops, deu problema com a xAI! Tenta de novo!"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, escape_markdown_v2(response_text), parse_mode='MarkdownV2')
    except Exception as e:
        error_msg = f"[ERROR] Inesperado: {str(e)}"
        logging.error(error_msg)
        response_text = "Deu uma zica aqui, brother! Tenta depois!"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, escape_markdown_v2(response_text), parse_mode='MarkdownV2')

# Busca no YouTube
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
        error_msg = f"[ERROR] YouTube API: {str(e)}"
        logging.error(error_msg)
        response_text = "Erro ao acessar a API do YouTube."
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(chat_id=message.chat.id, text=response_text)
    except Exception as e:
        error_msg = f"[ERROR] Inesperado /youtube: {str(e)}"
        logging.error(error_msg)
        response_text = "Erro inesperado ao buscar no YouTube."
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(chat_id=message.chat.id, text=response_text)

# Busca no Google
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
        error_msg = f"[ERROR] Google API: {str(e)}"
        logging.error(error_msg)
        response = "Desculpe, ocorreu um erro ao acessar a API do Google. Por favor, tente novamente mais tarde."
        logging.info(f"Resposta para @{username}: {response}")
        bot.send_message(chat_id=message.chat.id, text=response)

# Comandos de cotação
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
        error_msg = f"[ERROR] Euro API: {str(e)}"
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
        error_msg = f"[ERROR] Dolar API: {str(e)}"
        logging.error(error_msg)
        response_text = "Erro ao consultar a cotação do dólar. Tente novamente!"
        logging.info(f"Resposta para @{username}: {response_text}")
        bot.send_message(message.chat.id, response_text)

# Função auxiliar para formatar preços
def format_price(price: float) -> str:
    return f"{price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# Comandos de cotação coincap
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

# Comandos de ajuda e gerenciamento de frases
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

@bot.message_handler(func=lambda message: message.chat.type != 'private' and
                    message.text is not None and
                    "boa cabelo" in message.text.lower())
def responder_boa_cabelo(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    response = "vlw barba"
    logging.info(f"Resposta para @{username}: {response}")
    bot.reply_to(message, response)

def responder_imagem(message):
    username = message.from_user.username or "Unknown"
    logging.info(f"Mensagem recebida de @{username}: {message.text}")
    inicio = time.time()
    prompt = message.text[len('/imagem'):].strip()

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

# Inicia o bot
bot.polling()
