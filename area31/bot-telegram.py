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
logging.basicConfig(filename='bot-telegram.log', level=logging.INFO, format='%(asctime)s - %(message)s')
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
                # Início de um bloco de código
                code_block_open = True
                i += 3
                # Capturar a linguagem (se houver) até a próxima quebra de linha
                lang = ""
                while i < length and text[i] != "\n":
                    lang += text[i]
                    i += 1
                if i < length:
                    i += 1  # Pular a quebra de linha após a linguagem
                result += "```" + lang + "\n"
                # Acumular o conteúdo até o próximo ```
                while i < length:
                    if i + 2 < length and text[i:i+3] == "```":
                        i += 3
                        code_block_open = False
                        result += code_block_content.rstrip() + "```"
                        code_block_content = ""
                        break
                    # Escapar \ dentro do bloco de código
                    if text[i] == "\\":
                        code_block_content += "\\\\"
                    else:
                        code_block_content += text[i]
                    i += 1
            else:
                # Ignorar ``` extras dentro do bloco
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

    # Fechar bloco de código se ainda estiver aberto
    if code_block_open:
        result += code_block_content.rstrip() + "```"

    # Garantir que o resultado nunca seja vazio
    if not result:
        result = "Erro ao processar formatação, tente novamente."

    # Truncar para o limite do Telegram
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
BOT_AI = config_bot['DEFAULT'].get('BOT_AI', 'openai')  # Default para OpenAI se não especificado

# Parâmetros gerais
MAX_TOKENS = 1000  # Mantido para dar espaço, mas o prompt controlará o tamanho
TEMPERATURE = 0.8
OPENAI_MODEL = "gpt-4"  # Ou "gpt-3.5-turbo"
# Default grok2-latest caso nao seja especificado no bot-telegram.cfg
XAI_MODEL = config_bot['DEFAULT'].get('XAI_MODEL', 'grok-2-latest')

def count_tokens(messages):
    total_tokens = 0
    for msg in messages:
        total_tokens += len(msg["content"].split()) + 10
    return total_tokens

# Prompt ajustado para limitar o tamanho da resposta
def get_prompt() -> str:
    try:
        with open('prompt.cfg', 'r', encoding='utf-8') as arquivo:
            base_prompt = arquivo.read().strip()
    except FileNotFoundError:
        base_prompt = ("Você é um assistente útil e espirituoso em um bot do Telegram. "
                       "Responda de forma natural, como um amigo conversando, mantendo o contexto da conversa. "
                       "Quando o usuário pedir para 'armazenar a info', guarde a informação em uma lista associada ao ID do usuário. "
                       "Quando perguntado 'quais são as infos que te pedi pra armazenar?', responda com a lista de informações armazenadas.")
    # Adicionar instrução de limite de caracteres
    return f"{base_prompt}\n\nSua resposta deve ter no máximo 4000 caracteres para caber no limite do Telegram (4096 caracteres, incluindo formatação). Se necessário, resuma ou ajuste o conteúdo para não ultrapassar esse limite."

# Xingamentos
@bot.message_handler(commands=['xinga'])
def random_message(message):
    logging.info("Comando /xinga chamado.")
    conn = sqlite3.connect('frases.db')
    c = conn.cursor()
    c.execute("SELECT frase FROM frases")
    frases = c.fetchall()
    conn.close()

    if not frases:
        bot.send_message(message.chat.id, 'Não há frases cadastradas.')
        return

    frase_escolhida = random.choice(frases)[0]

    if message.reply_to_message and hasattr(message.reply_to_message, 'from_user'):
        bot.reply_to(message.reply_to_message, frase_escolhida)
        logging.info(f"Comando /xinga chamado por @{message.from_user.username} como reply. Resposta: {frase_escolhida}")
    else:
        command_parts = message.text.split(maxsplit=2)
        if len(command_parts) > 1 and command_parts[1].startswith('@'):
            resposta = "{} {}".format(command_parts[1], frase_escolhida)
        else:
            resposta = frase_escolhida

        bot.send_message(message.chat.id, resposta)
        logging.info(f"Comando /xinga chamado por @{message.from_user.username}. Resposta: {resposta}")

# ChatGPT/xAI - Funções Auxiliares
def update_chat_memory(message):
    chat_id = message.chat.id
    if chat_id not in chat_memory:
        chat_memory[chat_id] = []
    
    role = "user" if message.from_user.id != bot.get_me().id else "assistant"
    chat_memory[chat_id].append({"role": role, "content": message.text})
    
    if len(chat_memory[chat_id]) > 10:
        chat_memory[chat_id] = chat_memory[chat_id][-10:]

def get_chat_history(message, reply_limit: int = 4) -> list:
    chat_id = message.chat.id
    history = []

    if message.reply_to_message:
        current_message = message
        history.append({"role": "user", "content": current_message.text})
        while current_message.reply_to_message and len(history) < reply_limit + 1:
            previous_message = current_message.reply_to_message
            role = "assistant" if previous_message.from_user.id == bot.get_me().id else "user"
            history.append({"role": role, "content": previous_message.text})
            current_message = previous_message
        history.reverse()
    else:
        if chat_id in chat_memory:
            history = chat_memory[chat_id].copy()

    token_count = count_tokens(history)
    while token_count > MAX_TOKENS * 0.7:
        history.pop(0)
        token_count = count_tokens(history)

    return history

def clear_stored_info(user_id):
    if user_id in stored_info:
        del stored_info[user_id]
        logging.info(f"Informações armazenadas limpas para user_id {user_id}")

@bot.message_handler(func=lambda message: message.text is not None and
                    message.from_user.id != bot.get_me().id and
                    (bot.get_me().username in message.text or
                     (message.reply_to_message is not None and
                      message.reply_to_message.from_user.id == bot.get_me().id)))
def responder(message):
    global start_time, request_count

    if message.chat.type == 'private' or message.from_user.is_bot:
        return

    current_time = time.time()
    if current_time - start_time > TIME_WINDOW:
        start_time = current_time
        request_count = 0
    if request_count >= REQUEST_LIMIT:
        bot.send_message(message.chat.id, escape_markdown_v2("Tô de boa, mas muito requisitado agora! Tenta de novo em uns segundos."), parse_mode='MarkdownV2')
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
                bot.reply_to(message, escape_markdown_v2("Opa, amigo! Armazenar o quê? Me dá algo pra guardar!"), parse_mode='MarkdownV2')
                return
            if user_id not in stored_info:
                stored_info[user_id] = []
            stored_info[user_id].append(info)
            logging.info(f"Info armazenada para user_id {user_id}: {info}")
            bot.reply_to(message, escape_markdown_v2("Beleza, guardei a info pra você!"), parse_mode='MarkdownV2')
            return
        except IndexError:
            bot.reply_to(message, escape_markdown_v2("Opa, amigo! Armazenar o quê? Me dá algo pra guardar!"), parse_mode='MarkdownV2')
            return

    if "quais são as infos que te pedi pra armazenar?" in text_lower or \
       "me diga o que pedi pra armazenar" in text_lower:
        if user_id in stored_info and stored_info[user_id]:
            resposta = "Olha só o que você me pediu pra guardar:\n" + "\n".join(stored_info[user_id])
        else:
            resposta = "Você ainda não me passou nenhuma info pra guardar, amigo!"
        bot.reply_to(message, escape_markdown_v2(resposta), parse_mode='MarkdownV2')
        return

    if "limpe tudo que armazenou" in text_lower:
        clear_stored_info(user_id)
        bot.reply_to(message, escape_markdown_v2("Feito, amigo! Tudo limpo, não guardei mais nada."), parse_mode='MarkdownV2')
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
        logging.info(f"Resposta gerada em {response_time:.2f}s usando {BOT_AI}: {answer}")

        if not answer or len(answer) < 3:
            answer = "Poxa, me deu um branco agora... deixa eu pensar melhor!"

        chat_memory[message.chat.id].append({"role": "assistant", "content": answer})
        answer_escaped = escape_markdown_v2(answer)
        logging.info(f"Texto escapado enviado ao Telegram: {answer_escaped}")

        if message.reply_to_message:
            bot.reply_to(message, answer_escaped, parse_mode='MarkdownV2')
        else:
            bot.send_message(message.chat.id, answer_escaped, parse_mode='MarkdownV2')

    except OpenAIError as e:
        error_msg = f"Erro na API da OpenAI: {str(e)}"
        logging.error(error_msg)
        bot.send_message(message.chat.id, escape_markdown_v2("Ops, minha cabeça de IA deu tilt! Tenta de novo!"), parse_mode='MarkdownV2')
    except requests.exceptions.RequestException as e:
        error_msg = f"Erro na API da xAI: {str(e)}"
        logging.error(error_msg)
        bot.send_message(message.chat.id, escape_markdown_v2("Ops, deu problema com a xAI! Tenta de novo!"), parse_mode='MarkdownV2')
    except Exception as e:
        logging.error(f"Erro inesperado: {str(e)}")
        bot.send_message(message.chat.id, escape_markdown_v2("Deu uma zica aqui, brother! Tenta depois!"), parse_mode='MarkdownV2')

# Busca no YouTube
@bot.message_handler(commands=['youtube'])
def youtube_search_command(message):
    query = message.text.replace("/youtube", "").strip()
    if not query:
        bot.send_message(chat_id=message.chat.id, text="Por favor, execute o /youtube com algum termo de busca")
        return

    API_KEY = open("token-google.cfg").read().strip()
    search_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&q={query}&type=video&maxResults=5&key={API_KEY}"

    try:
        response = requests.get(search_url)
        response.raise_for_status()
        data = response.json()

        items = data.get("items", [])
        if not items:
            bot.send_message(chat_id=message.chat.id, text="Não foram encontrados resultados para a sua pesquisa.")
            return

        resposta = f"🔎 Resultados do YouTube para *{query}*:\n\n"
        for item in items:
            video_id = item["id"].get("videoId")
            titulo = item["snippet"].get("title")
            url = f"https://www.youtube.com/watch?v={video_id}"
            resposta += f"- {titulo}\n{url}\n\n"

        bot.send_message(chat_id=message.chat.id, text=resposta.strip())

    except requests.exceptions.RequestException as e:
        bot.send_message(chat_id=message.chat.id, text="Erro ao acessar a API do YouTube.")
        logging.error(f"Erro YouTube API: {str(e)}")
    except Exception as e:
        bot.send_message(chat_id=message.chat.id, text="Erro inesperado ao buscar no YouTube.")
        logging.error(f"Erro inesperado /youtube: {str(e)}")


# Busca no Google
@bot.message_handler(commands=['search'])
def search_command(message):
    query = message.text.replace("/search", "").strip()
    if not query:
        bot.send_message(chat_id=message.chat.id, text="Por favor, execute o /search com algum termo de busca")
        return

    API_KEY = open("token-google.cfg").read().strip()
    SEARCH_ENGINE_ID = open("token-google-engine.cfg").read().strip()
    search_url = f"https://www.googleapis.com/customsearch/v1?key={API_KEY}&cx={SEARCH_ENGINE_ID}&q={query}"

    try:
        results = requests.get(search_url).json()
        if results.get("searchInformation").get("totalResults") == "0":
            bot.send_message(chat_id=message.chat.id, text="Não foram encontrados resultados para a sua pesquisa.")
            return
        results = results["items"]
        response = "Resultados da pesquisa para '" + query + "': \n"
        for result in results[:5]:
            response += result["title"] + " - " + result["link"] + "\n"
    except (requests.exceptions.RequestException, KeyError) as e:
        response = "Desculpe, ocorreu um erro ao acessar a API do Google. Por favor, tente novamente mais tarde."

    bot.send_message(chat_id=message.chat.id, text=response)

# Comandos de cotação
@bot.message_handler(commands=['real'])
def reais_message(message):
    bot.send_message(message.chat.id, 'O real não vale nada, é uma bosta essa moeda estado de merda!')

@bot.message_handler(commands=['euro'])
def euro_message(message):
    url = 'https://economia.awesomeapi.com.br/all/EUR-BRL'
    r = requests.get(url)
    euro_data = r.json()
    valor_euro = euro_data['EUR']['bid']
    bot.send_message(message.chat.id, 'O valor atual do euro em reais é R$ ' + valor_euro)

@bot.message_handler(commands=['dolar'])
def dolar_message(message):
    url = 'https://economia.awesomeapi.com.br/all/USD-BRL'
    r = requests.get(url)
    dolar_data = r.json()
    valor_dolar = dolar_data['USD']['bid']
    bot.send_message(message.chat.id, 'O valor atual do dólar em reais é R$ ' + valor_dolar)

@bot.message_handler(commands=['btc'])
def bitcoin_price(message):
    url = "https://api.coincap.io/v2/assets/bitcoin"
    response = requests.get(url)
    data = response.json()
    price = round(float(data['data']['priceUsd']), 2)
    bot.send_message(message.chat.id, f'Cotação atual do Bitcoin em dolar: ${price}')

@bot.message_handler(commands=['xmr'])
def handle_btc(message):
    url = "https://api.coincap.io/v2/assets/monero"
    response = requests.get(url)
    data = response.json()
    price = round(float(data['data']['priceUsd']), 2)
    bot.send_message(message.chat.id, f'Cotação atual do Monero em dolar: ${price}')

# Comandos de ajuda e gerenciamento de frases
@bot.message_handler(commands=['ajuda'])
def help_message(message):
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
    bot.send_message(message.chat.id, help_text)

@bot.message_handler(commands=['add'])
def add_message(message):
    text = message.text.split()
    if len(text) < 2:
        bot.send_message(message.chat.id, 'Comando inválido. Use /add e insira o xingamento')
        return
    frase = text[1]
    if len(message.text.split(' ', 1)[1]) > 150:
        bot.send_message(message.chat.id, 'Xingamento muito longo, por favor use até 150 caracteres')
        return
    conn = sqlite3.connect('frases.db')
    c = conn.cursor()
    frase = message.text.split(' ', 1)[1]
    c.execute("INSERT INTO frases (frase) VALUES (?)", (frase,))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, 'Xingamento adicionado com sucesso! Seu zuero!')

@bot.message_handler(commands=['list'])
def list_message(message):
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
                bot.send_message(message.chat.id, 'Não há frases cadastradas.')
            else:
                for frase in frases:
                    message_enviada = False
                    bot.send_message(message.chat.id, 'Xingamentos cadastrados:')
                    chunk_size = 20
                    if not message_enviada:
                        for i in range(0, len(frases), chunk_size):
                            message = '\n'.join([f'{frase[0]}: {frase[1]}' for frase in frases[i:i+chunk_size]])
                            bot.send_message(message.chat.id, message)
                            time.sleep(5)
                            message_enviada = True
                    else:
                        message_enviada = True
                        break
                    message_enviada = True
                    break
        else:
            bot.send_message(message.chat.id, 'Apenas o administrador e o dono do grupo podem executar este comando')
    else:
        bot.send_message(message.chat.id, 'Este comando não pode ser usado em chats privados')

@bot.message_handler(commands=['remover'])
def remover_message(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if message.chat.type != 'private':
        admin_ids = [admin.user.id for admin in bot.get_chat_administrators(chat_id) if admin.status != 'creator']
        owner_id = [admin for admin in bot.get_chat_administrators(chat_id) if admin.status == 'creator'][0].user.id
        if user_id == owner_id or user_id in admin_ids:
            frase_list = message.text.split()
            if len(frase_list) < 2:
                bot.send_message(message.chat.id, 'Insira um ID válido para remover')
                return
            frase_id = frase_list[1]
            if not frase_id.isdigit():
                bot.send_message(message.chat.id, 'Insira um ID válido para remover, ID é um número, seu MACACO!')
                return
            frase = frase_list[1]
            conn = sqlite3.connect('frases.db')
            c = conn.cursor()
            c.execute("DELETE FROM frases WHERE ID = ?", (frase,))
            conn.commit()
            conn.close()
            bot.send_message(message.chat.id, 'Xingamento removido com sucesso!')
        else:
            bot.send_message(message.chat.id, 'Somente o dono do grupo e administradores podem executar este comando.')
    else:
        bot.send_message(message.chat.id, 'Este comando não pode ser executado em conversas privadas.')

@bot.message_handler(func=lambda message: message.chat.type != 'private' and
                    message.text is not None and
                    "boa cabelo" in message.text.lower())
def responder_boa_cabelo(message):
    bot.reply_to(message, "vlw barba")
    logging.info(f"Resposta 'vlw barba' enviada para '{message.text}' por @{message.from_user.username}")

#########################################################################################

def responder_imagem(message):
    inicio = time.time()
    prompt = message.text[len('/imagem'):].strip()

    if not prompt:
        bot.reply_to(message, "Por favor, forneça uma descrição para a imagem. Exemplo: /imagem porco deitado na grama")
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
            bot.send_photo(message.chat.id, image_url, caption=legenda, reply_to_message_id=message.message_id)
            logging.info(f"Imagem gerada com sucesso em {duracao} segundos para prompt: '{prompt}' por @{message.from_user.username}")
        else:
            bot.reply_to(message, "Não consegui obter a imagem gerada.")


    except requests.exceptions.HTTPError as e:
        try:
            erro_detalhes = response.json()
            mensagem_erro = erro_detalhes.get("error", "Erro desconhecido.")
            codigo_erro = erro_detalhes.get("code", "sem código")
            resposta_formatada = f"❌ Erro ao gerar imagem:\n{mensagem_erro}\n\n(código: {response.status_code} - {codigo_erro})"
        except Exception:
            resposta_formatada = f"❌ Erro ao chamar a API da xAI: {str(e)}"
        bot.reply_to(message, resposta_formatada)
        logging.error(f"Erro HTTP: {response.status_code} - {response.text}")

    except Exception as e:
        bot.reply_to(message, f"Ocorreu um erro: {str(e)}")
        logging.error(f"Erro geral: {str(e)}")

    logging.info(f"Tentativa de gerar imagem para '{prompt}' por @{message.from_user.username}")

@bot.message_handler(func=lambda message: message.text.lower().startswith('/imagem'))
def handle_imagem(message):
    responder_imagem(message)



#########################################################################################
# Inicia o bot
bot.polling()
