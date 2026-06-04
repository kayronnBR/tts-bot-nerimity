import asyncio
import os
import pygame
import edge_tts
from nerimity_sdk import Bot

# --- CONFIGURAÇÕES ---
TOKEN = "BOT TOKEN"
CANAL_ID = "CANAL DE VOZ"
VOZ = "pt-BR-AntonioNeural" # Voz masculina padrão

# Inicializa o player de áudio do Pygame
pygame.mixer.init()

# Inicializa o Bot do Nerimity
bot = Bot(token=TOKEN)

async def gerar_e_tocar_audio(texto):
    """Transforma texto em fala realista e reproduz"""
    output_file = "tts_temp.mp3"
    
    # Gera o áudio usando a IA da Microsoft
    communicate = edge_tts.Communicate(texto, VOZ)
    await communicate.save(output_file)
    
    # Toca o áudio no computador
    pygame.mixer.music.load(output_file)
    pygame.mixer.music.play()
    
    # Espera o áudio terminar
    while pygame.mixer.music.get_busy():
        await asyncio.sleep(0.1)
        
    # Descarrega o arquivo e remove o temporário
    pygame.mixer.music.unload()
    if os.path.exists(output_file):
        os.remove(output_file)

@bot.on("message:created")
async def on_message(event):
    """Função que roda automaticamente a cada mensagem nova no chat"""
    msg = event.message
    
    # Verifica se a mensagem veio do canal correto
    if str(msg.channel_id) == str(CANAL_ID):
        
        # Coleta o nome do usuário usando o campo correto (created_by)
        autor = "Alguém"
        if hasattr(msg, "created_by") and msg.created_by:
            autor = getattr(msg.created_by, "username", "Alguém")
            
            # Evita que o bot leia as próprias mensagens (loop infinito)
            if hasattr(bot, "user") and bot.user and msg.created_by.id == bot.user.id:
                return

        conteudo = getattr(msg, "content", "")
        
        # Se houver texto, monta a frase perfeita e fala
        if conteudo:
            texto_para_falar = f"{autor} disse: {conteudo}"
            print(f"👉 Lendo no chat: {texto_para_falar}")
            
            # Executa a função do TTS
            await gerar_e_tocar_audio(texto_para_falar)

if __name__ == "__main__":
    print("🔄 Iniciando o Bot com o SDK oficial...")
    # Roda o bot e o mantém conectado escutando o servidor
    bot.run()
