# Telegram-bot-xingamentos

Um bot muito zuero pra você que já sabe como funciona um bot do telegram. RTFM!

## Installation
```bash
git clone https://github.com/area31/telegram-bot-xingamentos.git
```

## Install deps:

```bash
pip3 install pyTelegramBotAPI --user
```

## Usage
```bash
python area31/bot-telegram.py
```

## Import DB using txt file
```bash
bash area31/script-import.sh
```

## Install OpenRC init (Funtoo and Gentoo)
```bash
cp etc/init.d/bot-telegram-area31 /etc/init.d/bot-telegram-area31
rc-update add bot-telegram-area31 default
```

## Contributing

Pull requests are welcome. For major changes, please open an issue first
to discuss what you would like to change.

Please make sure to update tests as appropriate.

## License

[BSD 3-Clause License](https://opensource.org/licenses/BSD-3-Clause)
