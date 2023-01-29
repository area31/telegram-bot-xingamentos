import configparser
import time
import random
import sqlite3
import requests
import sys
from typing import Tuple
sys.path.append('/home/morfetico/.local/lib/python3.10/site-packages/')
import telebot

def create_table():
    # Conectando ao banco de dados
    with sqlite3.connect('frases.db') as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS frases (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            frase TEXT NOT NULL
        );
        """)

def insert_frase(frase:str):
    with sqlite3.connect('frases.db') as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO frases (frase) VALUES (?)", (frase,))

def get_random_frase()->str:
    with sqlite3.connect('frases.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT frase FROM frases ORDER BY RANDOM() LIMIT 1")
        return cursor.fetchone()[0]

create_table()

config = configparser.ConfigParser()
config.read('token-telegram.cfg')
TOKEN = config['DEFAULT']['TOKEN']
bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['real'])
def reais_message(message):
    bot.send_message(message.chat.id, 'O real não vale nada, é uma bosta essa moeda estatal de merda!')

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
    url = 'https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd'
    response = requests.get(url)
    data = response.json()
    btc_usd = data['bitcoin']['usd']
    bot.send_message(message.chat.id, f'Cotação atual do Bitcoin: ${btc_usd}')

@bot.message_handler(commands=['xmr'])
def handle_btc(message):
    url = "https://api.coincap.io/v2/assets/monero"
    response = requests.get(url)
    data = response.json()
    price = data['data']['priceUsd']
    bot.send_message(message.chat.id, f'Cotação atual do Monero: ${price}')

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
    help_text += '/real - Comando desnecessário pelo óbvio, mas tente executar pra ver...'
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
                    mensagem_enviada = False
                    bot.send_message(message.chat.id, 'Xingamentos cadastrados:')
                    chunk_size = 20  # numero de frases por mensagem
                    if not mensagem_enviada:
                        for i in range(0, len(frases), chunk_size):
                            mensagem = '\n'.join([f'{frase[0]}: {frase[1]}' for frase in frases[i:i+chunk_size]])
                            bot.send_message(message.chat.id, mensagem)
                            time.sleep(5)
                            mensagem_enviada = True
                    else:
                        mensagem_enviada = True
                        break

                    mensagem_enviada = True
                    break

        else:
            bot.send_message(message.chat.id, 'Apenas o administrador e o dono do grupo podem executar este comando')
    else:
        bot.send_message(message.chat.id, 'Este comando não pode ser usado em chats privados')

@bot.message_handler(commands=['remover'])
def remover_message(message):
    frase_list = message.text.split()
    if len(frase_list) < 2:
        bot.send_message(message.chat.id, 'Insira uma frase válida para remover')
        return
    frase = frase_list[1]
    conn = sqlite3.connect('frases.db')
    c = conn.cursor()
    c.execute("DELETE FROM frases WHERE ID = ?", (frase,))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, 'Xingamento removido com sucesso!')


@bot.message_handler(commands=['xinga'])
def random_message(message):
    conn = sqlite3.connect('frases.db')
    c = conn.cursor()
    c.execute("SELECT frase FROM frases")
    frases = c.fetchall()
    conn.close()
    if not frases:
        bot.send_message(message.chat.id, 'Não há frases cadastradas.')
    else:
        frase_escolhida = random.choice(frases)[0]
        bot.send_message(message.chat.id, frase_escolhida)

bot.polling()

