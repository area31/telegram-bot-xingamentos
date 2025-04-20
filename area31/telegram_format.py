# telegram_format.py
import html
from telebot import TeleBot

# Formatação HTML
def escape_html(text: str) -> str:
    """Escapa <, > e & para uso em parse_mode=HTML."""
    return html.escape(text, quote=False)

def bold(text: str) -> str:
    return f"<b>{escape_html(text)}</b>"

def italic(text: str) -> str:
    return f"<i>{escape_html(text)}</i>"

def underline(text: str) -> str:
    return f"<u>{escape_html(text)}</u>"

def code(text: str) -> str:
    return f"<code>{escape_html(text)}</code>"

def link(text: str, url: str) -> str:
    return f'<a href="{html.escape(url)}">{escape_html(text)}</a>'

def list_items(items: list[str]) -> str:
    """Recebe lista de linhas já escapadas e retorna com <br/> antes de cada."""
    return "<br/>".join(items)

# Formatação MarkdownV2
def escape_markdown_v2(text: str) -> str:
    """
    Escapa caracteres reservados do MarkdownV2, exceto marcadores de formatação.
    Garante que *texto*, **texto**, ~texto~, etc., sejam preservados.
    """
    reserved_chars = r'[]()~`>#+-=|{.!'  # Exclui *, _, ` de reserved_chars pra preservar formatação
    return ''.join('\\' + c if c in reserved_chars else c for c in text)

def bold_md(text: str) -> str:
    return f"*{escape_markdown_v2(text)}*"

def italic_md(text: str) -> str:
    return f"_{escape_markdown_v2(text)}_"

def strikethrough_md(text: str) -> str:
    return f"~{escape_markdown_v2(text)}~"

def underline_md(text: str) -> str:
    return f"__{escape_markdown_v2(text)}__"

def spoiler_md(text: str) -> str:
    return f"||{escape_markdown_v2(text)}||"

def code_md(text: str) -> str:
    return f"`{escape_markdown_v2(text)}`"

def latex_inline(text: str) -> str:
    """Formata LaTeX inline com \(...\)."""
    return f"\\({text}\\)"

def latex_display(text: str) -> str:
    """Formata LaTeX em bloco com $$...$$."""
    return f"$${text}$$"

# Funções de envio
def send_html(bot: TeleBot, chat_id: int, html_text: str, **kwargs):
    """
    Envia mensagem com parse_mode=HTML.
    Define disable_web_page_preview=True por padrão.
    """
    bot.send_message(
        chat_id,
        html_text,
        parse_mode="HTML",
        disable_web_page_preview=kwargs.pop("disable_web_page_preview", True),
        **kwargs
    )

def send_markdown(bot: TeleBot, chat_id: int, md_text: str, **kwargs):
    """
    Envia mensagem com parse_mode=MarkdownV2.
    Define disable_web_page_preview=True por padrão.
    """
    bot.send_message(
        chat_id,
        md_text,
        parse_mode="MarkdownV2",
        disable_web_page_preview=kwargs.pop("disable_web_page_preview", True),
        **kwargs
    )
