import configparser
import random
import sqlite3
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

start = False

@bot.message_handler(commands=['start'])
def start_message(message):
    global start
    start = True
    bot.send_message(message.chat.id, 'Bot iniciado!')

@bot.message_handler(commands=['ajuda'])
def help_message(message):
    bot.send_message(message.chat.id, 'Comandos disponíveis:')
    bot.send_message(message.chat.id, '/start - Inicia o bot')
    bot.send_message(message.chat.id, '/add - Adiciona um xingamento, mas seja insolente por favor')
    bot.send_message(message.chat.id, '/list - Lista os xingamentos cadastrados')
    bot.send_message(message.chat.id, '/remover - Remove um xingamento')
    bot.send_message(message.chat.id, '/xinga - envia um xingamento aleatório')

@bot.message_handler(commands=['add'])
def add_message(message):
    if not start:
        return
    conn = sqlite3.connect('frases.db')
    c = conn.cursor()
    frase = message.text.split(' ', 1)[1]
    c.execute("INSERT INTO frases (frase) VALUES (?)", (frase,))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, 'Frase adicionada com sucesso!')

@bot.message_handler(commands=['list'])
def list_message(message):
    if not start:
        return
    conn = sqlite3.connect('frases.db')
    c = conn.cursor()
    c.execute("SELECT id, frase FROM frases")
    frases = c.fetchall()
    conn.close()
    if not frases:
        bot.send_message(message.chat.id, 'Não há frases cadastradas.')
    else:
        bot.send_message(message.chat.id, 'Frases cadastradas:')
        for frase in frases:
            bot.send_message(message.chat.id, f'{frase[0]}: {frase[1]}')

@bot.message_handler(commands=['remover'])
def remover_message(message):
    if not start:
        return
    conn = sqlite3.connect('frases.db')
    c = conn.cursor()
    frase = message.text.split()[1]

    c.execute("DELETE FROM frases WHERE ID = ?", (frase,))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, 'Frase removida com sucesso!')

@bot.message_handler(commands=['xinga'])
def random_message(message):
    if not start:
        return
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

