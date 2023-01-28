#!/bin/bash

# nome do banco de dados
DB_NAME="frases.db"

# ler cada linha do arquivo
while read line; do
    # inserir linha no banco de dados
    sqlite3 $DB_NAME "INSERT INTO frases (frase) VALUES ('$line');"
done < frases.txt

echo "Todas as linhas do arquivo foram adicionadas ao banco de dados"

