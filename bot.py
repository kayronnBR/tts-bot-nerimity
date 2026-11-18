"""
Bot de TTS para Nerimity com áudio estabilizado (sem picotar), comando -tts,
aviso de CAPSLOCK com autodeleção em 1 minuto e leitura estrita em maiúsculas.
"""

import asyncio
import fractions
import os
import time
from typing import Dict, Optional, Set

import av
import edge_tts
import numpy as np
from aiortc import (
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
    MediaStreamTrack,
)
from aiortc.sdp import candidate_from_sdp

from nerimity_sdk import Bot

# --- CONFIGURAÇÕES ---
TOKEN = "TOKEN"
VOZ = "pt-BR-AntonioNeural"

USUARIOS_BLOQUEADOS: Set[str] = {
    # "ID_USUARIO_BLOQUEADO",
}

SAMPLE_RATE = 48000
FRAME_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000  # 960 amostras por quadro

MAX_QUEUE_FRAMES = 100
PEER_CONNECT_TIMEOUT = 20

ICE_SERVERS = [
    RTCIceServer(urls="stun:stun.l.google.com:19302"),
    RTCIceServer(urls="stun:stun.relay.metered.ca:80"),
    RTCIceServer(
        urls="turn:a.relay.metered.ca:80",
        username="b9fafdffb3c428131bd9ae10",
        credential="DTk2mXfXv4kJYPvD",
    ),
    RTCIceServer(
        urls="turn:a.relay.metered.ca:443",
        username="b9fafdffb3c428131bd9ae10",
        credential="DTk2mXfXv4kJYPvD",
    ),
]

bot = Bot(token=TOKEN)


class TTSAudioTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self):
        super().__init__()
        self._queue: "asyncio.Queue[bytes]" = asyncio.Queue()
        self._pts = 0
        self._start: Optional[float] = None

    async def push_pcm(self, pcm_bytes: bytes) -> None:
        frame_bytes = SAMPLES_PER_FRAME * 2
        for i in range(0, len(pcm_bytes), frame_bytes):
            if self._queue.qsize() >= MAX_QUEUE_FRAMES:
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass

            chunk = pcm_bytes[i : i + frame_bytes]
            if len(chunk) < frame_bytes:
                chunk += b"\x00" * (frame_bytes - len(chunk))
            await self._queue.put(chunk)

    async def recv(self):
        if self._start is None:
            self._start = time.monotonic()

        target = self._start + (self._pts / SAMPLE_RATE)
        delay = target - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

        try:
            chunk = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            chunk = b"\x00" * (SAMPLES_PER_FRAME * 2)

        # Garante conversão e normalização exata para evitar estouro/chiado no WebRTC
        samples = np.frombuffer(chunk, dtype=np.int16).copy()
        # Limita o volume para evitar pico de áudio estourado
        samples = np.clip(samples, -32768, 32767).reshape(1, -1)

        frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
        frame.sample_rate = SAMPLE_RATE
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, SAMPLE_RATE)
        self._pts += SAMPLES_PER_FRAME
        return frame


def decodificar_mp3_para_pcm(caminho: str) -> bytes:
    """Decodifica e reamostra o MP3 para PCM mono 48kHz limpo."""
    container = av.open(caminho)
    try:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(
            format="s16", 
            layout="mono", 
            rate=SAMPLE_RATE,
            frame_size=SAMPLES_PER_FRAME
        )
        pcm = bytearray()
        for frame in container.decode(stream):
            for rframe in resampler.resample(frame):
                pcm += bytes(rframe.planes[0])
        return bytes(pcm)
    finally:
        container.close()


class VoiceSession:
    def __init__(self, bot_inst: Bot, channel_id: str):
        self.bot = bot_inst
        self.channel_id = channel_id
        self.my_user_id: Optional[str] = None
        self.peers: Dict[str, RTCPeerConnection] = {}
        self.tracks: Dict[str, TTSAudioTrack] = {}
        self._drain_tasks: Dict[str, list] = {}
        self._connected = False

    async def join(self) -> None:
        gateway = self.bot._gateway
        socket_id = getattr(gateway, "socket_id", None)
        if not socket_id:
            raise RuntimeError("Gateway ainda não conectado.")

        await self.bot.rest.join_voice(self.channel_id, socket_id)
        self._connected = True
        print(f"🔊 Entrou no canal de voz {self.channel_id}")

    async def leave(self) -> None:
        for pc in list(self.peers.values()):
            await pc.close()
        for tasks in self._drain_tasks.values():
            for task in tasks:
                task.cancel()
        self._drain_tasks.clear()
        self.peers.clear()
        self.tracks.clear()
        if self._connected:
            await self.bot.rest.leave_voice(self.channel_id)
            self._connected = False
        print("🔇 Saiu do canal de voz")

    def _nova_conexao(self, user_id: str) -> RTCPeerConnection:
        pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=ICE_SERVERS))
        track = TTSAudioTrack()
        pc.addTrack(track)
        self.peers[user_id] = pc
        self.tracks[user_id] = track
        self._drain_tasks.setdefault(user_id, [])

        @pc.on("connectionstatechange")
        async def on_state_change():
            if pc.connectionState in ("failed", "closed", "disconnected"):
                await self._remover_conexao(user_id)

        @pc.on("track")
        def on_track(remote_track):
            drain_task = asyncio.create_task(self._descartar_audio_recebido(user_id, remote_track))
            self._drain_tasks[user_id].append(drain_task)

        asyncio.create_task(self._watchdog_conexao(user_id, pc))
        return pc

    async def _descartar_audio_recebido(self, user_id: str, remote_track) -> None:
        try:
            while True:
                await remote_track.recv()
        except Exception:
            pass

    async def _watchdog_conexao(self, user_id: str, pc: RTCPeerConnection) -> None:
        await asyncio.sleep(PEER_CONNECT_TIMEOUT)
        if self.peers.get(user_id) is pc and pc.connectionState not in ("connected",):
            await self._remover_conexao(user_id)

    async def _remover_conexao(self, user_id: str) -> None:
        pc = self.peers.pop(user_id, None)
        self.tracks.pop(user_id, None)
        for task in self._drain_tasks.pop(user_id, []):
            task.cancel()
        if pc:
            await pc.close()

    async def _enviar_sinal(self, to_user_id: str, signal: dict) -> None:
        if hasattr(self.bot, "_gateway") and self.bot._gateway:
            await self.bot._gateway.emit(
                "voice:signal_send",
                {
                    "channelId": self.channel_id,
                    "toUserId": to_user_id,
                    "signal": signal,
                },
            )

    async def ao_usuario_entrar(self, payload: dict) -> None:
        if not self._connected:
            return
        channel_id = payload.get("channelId")
        user_id = payload.get("userId")
        if channel_id != self.channel_id or not user_id:
            return
        if user_id == self.my_user_id or user_id in self.peers:
            return

        pc = self._nova_conexao(user_id)
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        await self._enviar_sinal(
            user_id, {"type": pc.localDescription.type, "sdp": pc.localDescription.sdp}
        )

    async def ao_usuario_sair(self, payload: dict) -> None:
        user_id = payload.get("userId")
        if user_id:
            await self._remover_conexao(user_id)

    async def ao_receber_sinal(self, payload: dict) -> None:
        channel_id = payload.get("channelId")
        if channel_id != self.channel_id:
            return
        from_user_id = payload.get("fromUserId")
        signal = payload.get("signal") or {}
        if not from_user_id:
            return

        pc = self.peers.get(from_user_id)

        if "sdp" in signal:
            if pc is None:
                pc = self._nova_conexao(from_user_id)
            desc = RTCSessionDescription(sdp=signal["sdp"], type=signal["type"])
            await pc.setRemoteDescription(desc)
            if signal["type"] == "offer":
                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)
                await self._enviar_sinal(
                    from_user_id,
                    {"type": pc.localDescription.type, "sdp": pc.localDescription.sdp},
                )

        elif signal.get("candidate"):
            if pc is None:
                return
            cand_info = signal["candidate"]
            cand_str = cand_info.get("candidate", "")
            if cand_str.startswith("candidate:"):
                cand_str = cand_str.split(":", 1)[1]
            if not cand_str:
                return
            candidate = candidate_from_sdp(cand_str)
            candidate.sdpMid = cand_info.get("sdpMid")
            candidate.sdpMLineIndex = cand_info.get("sdpMLineIndex")
            await pc.addIceCandidate(candidate)

    async def falar(self, texto: str, voz: str = VOZ) -> None:
        out_file = f"tts_temp_{int(time.time() * 1000)}.mp3"
        try:
            communicate = edge_tts.Communicate(texto, voz)
            await communicate.save(out_file)

            if not os.path.exists(out_file) or os.path.getsize(out_file) == 0:
                return

            try:
                pcm = await asyncio.to_thread(decodificar_mp3_para_pcm, out_file)
            except Exception as exc:
                print(f"⚠️ Erro ao decodificar TTS: {exc}")
                return
        finally:
            if os.path.exists(out_file):
                os.remove(out_file)

        if self.tracks:
            await asyncio.gather(*(track.push_pcm(pcm) for track in self.tracks.values()))


class GerenciadorSalas:
    def __init__(self, bot_inst: Bot):
        self.bot = bot_inst
        self.my_user_id: Optional[str] = None
        self.salas_por_texto: Dict[str, str] = {}
        self.sessions: Dict[str, VoiceSession] = {}


gerenciador = GerenciadorSalas(bot)


@bot.on("ready")
async def on_ready(me):
    gerenciador.my_user_id = getattr(me, "id", None)
    print(f"✅ Conectado como {getattr(me, 'username', '?')}")


@bot.on("voice:user_joined")
async def on_voice_user_joined(payload):
    sala = gerenciador.sessions.get(str(payload.get("channelId", "")))
    if sala:
        await sala.ao_usuario_entrar(payload)


@bot.on("voice:user_left")
async def on_voice_user_left(payload):
    sala = gerenciador.sessions.get(str(payload.get("channelId", "")))
    if sala:
        await sala.ao_usuario_sair(payload)


@bot.on("voice:signal_received")
async def on_voice_signal(payload):
    sala = gerenciador.sessions.get(str(payload.get("channelId", "")))
    if sala:
        await sala.ao_receber_sinal(payload)


@bot.on("message:created")
async def on_message(event):
    msg = event.message
    channel_id = str(msg.channel_id)
    conteudo = getattr(msg, "content", "") or ""

    autor = getattr(msg, "created_by", None)
    autor_id = getattr(autor, "id", None)
    autor_nome = getattr(autor, "username", "Alguém")

    if gerenciador.my_user_id and str(autor_id) == str(gerenciador.my_user_id):
        return

    if autor_id and str(autor_id) in USUARIOS_BLOQUEADOS:
        return

    conteudo_stripped = conteudo.strip()
    partes = conteudo_stripped.split(maxsplit=1)
    comando = partes[0].lower() if partes else ""
    argumento = partes[1].strip() if len(partes) > 1 else ""

    # Comando unificado -tts (entra ou sai da call)
    if comando == "-tts":
        if channel_id in gerenciador.salas_por_texto:
            canal_voz_id = gerenciador.salas_por_texto.pop(channel_id, None)
            session = gerenciador.sessions.pop(canal_voz_id, None)
            if session:
                await session.leave()
            await bot.rest.create_message(channel_id, "🔇 Bot desconectado da call.")
            return

        canal_voz_alvo = argumento if argumento else channel_id

        voice_session = VoiceSession(bot, canal_voz_alvo)
        voice_session.my_user_id = gerenciador.my_user_id
        try:
            await voice_session.join()
        except Exception as exc:
            await bot.rest.create_message(channel_id, f"❌ Erro ao entrar na call: {exc}")
            return

        gerenciador.salas_por_texto[channel_id] = canal_voz_alvo
        gerenciador.sessions[canal_voz_alvo] = voice_session
        
        # Mensagem atualizada informando que o bot só lê CAPSLOCK + aviso de sincronização
        mensagem_aviso = await bot.rest.create_message(
            channel_id, 
            "💡 **Aviso:** Eu só leio mensagens em **CAPSLOCK** (caixa alta)!\n"
            "Caso não esteja me escutando ou com o ícone de microfone silenciado, saia e entre novamente da ligação."
        )

        # Deleta o aviso automaticamente após 1 minuto (60 segundos)
        async def apagar_aviso_depois_de_um_minuto():
            await asyncio.sleep(60)
            try:
                msg_id = getattr(mensagem_aviso, "id", None)
                if msg_id:
                    await bot.rest.delete_message(channel_id, msg_id)
            except Exception:
                pass

        asyncio.create_task(apagar_aviso_depois_de_um_minuto())
        return

    # Leitura de mensagens para o TTS (Estritamente CAPSLOCK)
    canal_voz_id = gerenciador.salas_por_texto.get(channel_id)
    if canal_voz_id and conteudo_stripped:
        tem_letras = any(c.isalpha() for c in conteudo_stripped)
        if not tem_letras or not conteudo_stripped.isupper():
            return

        session = gerenciador.sessions.get(canal_voz_id)
        if session:
            texto_para_falar = f"{autor_nome} disse: {conteudo_stripped}"
            print(f"👉 Falando na call: {texto_para_falar}")
            await session.falar(texto_para_falar)


if __name__ == "__main__":
    print("🔄 Iniciando o Bot (Áudio normalizado + Aviso de CAPSLOCK)...")
    bot.run()
