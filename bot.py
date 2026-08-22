"""
Bot de TTS para Nerimity que ENTRA NA CALL DE VOZ e fala o texto do chat.

⚠️ AVISO IMPORTANTE
A Nerimity não expõe uma API pronta de "bot entra na call e manda áudio".
As chamadas de voz funcionam com WebRTC em malha (mesh): cada participante
troca sinalização (ofertas/respostas SDP e candidatos ICE) via Socket.IO
usando os eventos "voice:signal_send" / "voice:signal_received", tudo isso
espelhando o que o cliente web oficial (simple-peer) faz. Não existe
documentação pública desse protocolo — o que está aqui foi construído
inspecionando o pacote nerimity_sdk (nerimity_sdk/transport/rest.py e
gateway.py) e o código-fonte do cliente oficial em
https://github.com/Nerimity/nerimity-web (src/chat-api/store/useVoiceUsers.ts
e src/chat-api/emits/voiceEmits.ts).

Ou seja: isso é uma implementação "melhor esforço" baseada em engenharia
reversa. Se a Nerimity mudar o protocolo de sinalização no futuro, este
código pode parar de funcionar e precisar de ajustes.

DEPENDÊNCIAS (além do que você já tinha):
    pip install aiortc av numpy edge-tts nerimity_sdk

Não precisa mais do pygame — o áudio agora vai direto para a chamada de voz,
em vez de tocar no alto-falante do computador que roda o bot.
"""

import asyncio
import fractions
import os
import time
from typing import Dict, Optional

import av
import numpy as np
import edge_tts

from aiortc import (
    RTCConfiguration,
    RTCIceCandidate,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
    MediaStreamTrack,
)
from aiortc.sdp import candidate_from_sdp

from nerimity_sdk import Bot

# --- CONFIGURAÇÕES ---
TOKEN = "XXXXX"
VOZ = "pt-BR-AntonioNeural"  # Voz masculina padrão

# Comandos que qualquer pessoa (não bloqueada) pode mandar na DM do bot.
COMANDO_CONFIG = "!config"
COMANDO_SAIR = "!sair"   # !sair <id_do_canal_de_voz> -> tira o bot só daquela call

# --- CONTROLE DE ACESSO ---
# IDs de usuários que NUNCA podem usar os comandos do bot (!config / !sair).
# Adicione manualmente os IDs aqui, como texto, um por linha.
USUARIOS_BLOQUEADOS = {
    # "123456789012345678",
    # "987654321098765432",
}

# Comando de senha mestre: SEMPRE reseta TODAS as salas de uma vez (tira o
# bot de todas as calls), não importa quem manda - inclusive gente bloqueada
# - porque a autenticação aqui é pela senha, não pelo ID de quem enviou.
# É a ÚNICA forma de resetar tudo de uma vez; "!sair" só tira de uma call
# por vez. Troque o valor abaixo antes de rodar o bot; use algo só seu.
# Uso: mande "!master SUA_SENHA_AQUI" na DM do bot.
COMANDO_MESTRE = "!master"
SENHA_MESTRE = "TROQUE_ESSA_SENHA"

SAMPLE_RATE = 48000
FRAME_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000  # 960 amostras por quadro

# Limite de segurança: no máximo ~30s de áudio pendente por conexão.
# Se uma conexão travar sem consumir a fila, ela para de crescer aqui
# em vez de estourar a memória do computador.
MAX_QUEUE_FRAMES = (30 * 1000) // FRAME_MS

# Tempo máximo (segundos) que uma conexão pode ficar "connecting" antes
# de ser considerada morta e fechada automaticamente.
PEER_CONNECT_TIMEOUT = 20

# Servidores STUN/TURN usados pelo cliente oficial da Nerimity
# (retirados do código-fonte público de nerimity-web, useVoiceUsers.ts)
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
    """Faixa de áudio "ao vivo" que fica emitindo silêncio até receber
    PCM de fala pra tocar. Uma instância é criada por participante (cada
    RTCPeerConnection precisa da sua própria, não dá pra reaproveitar a
    mesma faixa em várias conexões)."""

    kind = "audio"

    def __init__(self):
        super().__init__()
        self._queue: "asyncio.Queue[bytes]" = asyncio.Queue()
        self._pts = 0
        self._start: Optional[float] = None

    async def push_pcm(self, pcm_bytes: bytes) -> None:
        frame_bytes = SAMPLES_PER_FRAME * 2  # 16 bits = 2 bytes/amostra
        for i in range(0, len(pcm_bytes), frame_bytes):
            # Trava de segurança: se a fila já está cheia (conexão travada,
            # não está consumindo), descarta o áudio mais velho em vez de
            # crescer sem limite e estourar a memória.
            if self._queue.qsize() >= MAX_QUEUE_FRAMES:
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass

            chunk = pcm_bytes[i:i + frame_bytes]
            if len(chunk) < frame_bytes:
                chunk += b"\x00" * (frame_bytes - len(chunk))
            await self._queue.put(chunk)

    async def recv(self):
        if self._start is None:
            self._start = time.monotonic()

        # mantém o ritmo de 20ms por quadro em tempo real
        target = self._start + (self._pts / SAMPLE_RATE)
        delay = target - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

        try:
            chunk = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            chunk = b"\x00" * (SAMPLES_PER_FRAME * 2)  # silêncio

        samples = np.frombuffer(chunk, dtype=np.int16).reshape(1, -1)
        frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
        frame.sample_rate = SAMPLE_RATE
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, SAMPLE_RATE)
        self._pts += SAMPLES_PER_FRAME
        return frame


def decodificar_mp3_para_pcm(caminho: str) -> bytes:
    """Decodifica o mp3 gerado pelo edge-tts para PCM 16-bit mono 48kHz.

    Se o arquivo vier vazio/corrompido (ex.: falha de rede no edge-tts),
    levanta a exceção de volta para quem chamou tratar — mas garante que
    o container do PyAV/FFmpeg é sempre fechado, mesmo em erro, pra não
    vazar memória/descritores de arquivo a cada falha.
    """
    container = av.open(caminho)
    try:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        pcm = bytearray()
        for frame in container.decode(stream):
            for rframe in resampler.resample(frame):
                pcm += bytes(rframe.planes[0])
        return bytes(pcm)
    finally:
        container.close()


class VoiceSession:
    """Gerencia a presença do bot em UM canal de voz: entra, negocia WebRTC
    com cada participante (malha peer-to-peer) e distribui o áudio falado."""

    def __init__(self, bot: Bot, channel_id: str):
        self.bot = bot
        self.channel_id = channel_id
        self.my_user_id: Optional[str] = None
        self.peers: Dict[str, RTCPeerConnection] = {}
        self.tracks: Dict[str, TTSAudioTrack] = {}
        self._drain_tasks: Dict[str, list] = {}
        self._connected = False

    # -- ciclo de vida --------------------------------------------------

    async def join(self) -> None:
        gateway = self.bot._gateway  # acesso "por baixo dos panos" do SDK
        socket_id = gateway.socket_id
        if not socket_id:
            raise RuntimeError("Gateway ainda não conectado; entre na voz depois do evento 'ready'.")

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

    # -- WebRTC -----------------------------------------------------------

    def _nova_conexao(self, user_id: str) -> RTCPeerConnection:
        pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=ICE_SERVERS))
        track = TTSAudioTrack()
        pc.addTrack(track)
        self.peers[user_id] = pc
        self.tracks[user_id] = track
        self._drain_tasks.setdefault(user_id, [])

        @pc.on("connectionstatechange")
        async def on_state_change():
            print(f"[RTC] {user_id}: {pc.connectionState}")
            if pc.connectionState in ("failed", "closed", "disconnected"):
                await self._remover_conexao(user_id)

        @pc.on("track")
        def on_track(remote_track):
            # IMPORTANTE: o aiortc guarda o áudio recebido de cada participante
            # numa fila SEM LIMITE (RemoteStreamTrack._queue). Se ninguém
            # nunca chama .recv() nela, essa fila cresce pra sempre enquanto
            # a pessoa fica na call - é a maior causa de vazamento de memória
            # aqui. Como o bot só precisa FALAR (não escutar), a gente
            # consome e descarta esse áudio continuamente pra fila nunca
            # acumular.
            print(f"[RTC] {user_id}: recebendo faixa '{remote_track.kind}', descartando (bot não escuta).")
            drain_task = asyncio.create_task(self._descartar_audio_recebido(user_id, remote_track))
            self._drain_tasks[user_id].append(drain_task)

        # Watchdog: se essa conexão nunca sair de "connecting"/"new" dentro
        # do prazo, ela é considerada travada (zumbi) e é fechada sozinha —
        # evita que fiquem se acumulando pra sempre em memória.
        asyncio.create_task(self._watchdog_conexao(user_id, pc))

        return pc

    async def _descartar_audio_recebido(self, user_id: str, remote_track) -> None:
        try:
            while True:
                await remote_track.recv()
        except Exception:
            # a faixa terminou (usuário saiu, mudou de mic, conexão fechou etc.)
            pass

    async def _watchdog_conexao(self, user_id: str, pc: RTCPeerConnection) -> None:
        await asyncio.sleep(PEER_CONNECT_TIMEOUT)
        if self.peers.get(user_id) is pc and pc.connectionState not in ("connected",):
            print(f"[RTC] {user_id}: conexão travada em '{pc.connectionState}', fechando.")
            await self._remover_conexao(user_id)

    async def _remover_conexao(self, user_id: str) -> None:
        pc = self.peers.pop(user_id, None)
        self.tracks.pop(user_id, None)
        for task in self._drain_tasks.pop(user_id, []):
            task.cancel()
        if pc:
            await pc.close()

    async def _enviar_sinal(self, to_user_id: str, signal: dict) -> None:
        await self.bot._gateway.emit(
            "voice:signal_send",
            {
                "channelId": self.channel_id,
                "toUserId": to_user_id,
                "signal": signal,
            },
        )

    async def ao_usuario_entrar(self, payload: dict) -> None:
        """Alguém entrou no canal enquanto o bot já estava lá -> o bot inicia
        a oferta (mesma lógica do cliente oficial: quem já está na call
        inicia a conexão com quem chega)."""
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
                return  # candidato chegou antes da oferta/resposta; ignora
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

    # -- fala ---------------------------------------------------------------

    async def falar(self, texto: str, voz: str = VOZ) -> None:
        """Gera o TTS e transmite pra todo mundo conectado na call."""
        if not self.tracks:
            print("⚠️ Ninguém conectado na call ainda (ou negociação em andamento); nada pra ouvir.")

        out_file = f"tts_temp_{int(time.time() * 1000)}.mp3"
        try:
            communicate = edge_tts.Communicate(texto, voz)
            await communicate.save(out_file)

            if not os.path.exists(out_file) or os.path.getsize(out_file) == 0:
                print("⚠️ edge-tts gerou um arquivo vazio (provável falha de rede); ignorando essa fala.")
                return

            try:
                # decodificar_mp3_para_pcm é bloqueante (I/O + CPU); rodar em
                # thread separada evita travar o loop de eventos do bot
                # (heartbeat, socket, outras mensagens) enquanto decodifica.
                pcm = await asyncio.to_thread(decodificar_mp3_para_pcm, out_file)
            except Exception as exc:
                # ex.: "packet queue is empty, aborting" - mp3 corrompido/
                # incompleto. Não deixa o bot travar nem vazar: só ignora
                # essa fala e segue funcionando.
                print(f"⚠️ Falha ao decodificar áudio do TTS, ignorando essa fala: {exc}")
                return
        finally:
            if os.path.exists(out_file):
                os.remove(out_file)

        await asyncio.gather(*(track.push_pcm(pcm) for track in self.tracks.values()))


class ConfigState:
    AGUARDANDO_TEXTO = "aguardando_texto"
    AGUARDANDO_VOZ = "aguardando_voz"


class Sala:
    """Uma sala = um par (canal de texto que o bot escuta) + (canal de voz
    onde ele fala). O bot pode ter várias salas ativas ao mesmo tempo, cada
    uma com sua própria VoiceSession — inclusive em servidores diferentes."""

    def __init__(self, canal_texto_id: str, canal_voz_id: str, voice_session: "VoiceSession"):
        self.canal_texto_id = canal_texto_id
        self.canal_voz_id = canal_voz_id
        self.voice_session = voice_session


class FluxoConfig:
    """Estado de uma configuração em andamento numa DM específica.
    Cada pessoa configurando uma sala nova tem o seu próprio fluxo,
    então várias pessoas podem estar configurando salas diferentes ao
    mesmo tempo, em servidores diferentes, sem interferir uma na outra."""

    def __init__(self, dm_channel_id: str, user_id: str):
        self.dm_channel_id = dm_channel_id
        self.user_id = user_id
        self.estado = ConfigState.AGUARDANDO_TEXTO
        self.canal_texto_id: Optional[str] = None


class GerenciadorSalas:
    """Gerencia todas as salas (calls) ativas do bot e os fluxos de
    configuração em andamento em cada DM.

    Comandos (mandados na DM do bot, por qualquer pessoa):
      !config              -> começa a configurar uma sala nova
      !reset <id_da_voz>   -> desliga só aquela sala específica
      !reset               -> desliga TODAS as salas ativas
    """

    def __init__(self, bot: Bot):
        self.bot = bot
        self.my_user_id: Optional[str] = None
        self.salas_por_texto: Dict[str, Sala] = {}   # canal_texto_id -> Sala
        self.salas_por_voz: Dict[str, Sala] = {}      # canal_voz_id  -> Sala
        self.fluxos: Dict[str, FluxoConfig] = {}       # dm_channel_id -> FluxoConfig

    async def _enviar_dm(self, channel_id: str, texto: str) -> None:
        if channel_id:
            await self.bot.rest.create_message(channel_id, texto)

    def sala_por_canal_texto(self, canal_texto_id: str) -> Optional[Sala]:
        return self.salas_por_texto.get(canal_texto_id)

    def sala_por_canal_voz(self, canal_voz_id: str) -> Optional[Sala]:
        return self.salas_por_voz.get(canal_voz_id)

    # -- configuração (DM) --------------------------------------------------

    async def iniciar_config(self, dm_channel_id: str, user_id: str) -> None:
        fluxo = FluxoConfig(dm_channel_id, user_id)
        self.fluxos[dm_channel_id] = fluxo
        await self._enviar_dm(
            dm_channel_id,
            "👋 Vamos criar uma sala nova! Me manda o **ID do canal de TEXTO** "
            "que devo escutar (onde vou ler as mensagens em CAIXA ALTA).",
        )

    async def processar_resposta_dm(self, dm_channel_id: str, texto: str) -> None:
        fluxo = self.fluxos.get(dm_channel_id)
        if not fluxo:
            return
        texto = (texto or "").strip()
        if not texto:
            return

        if fluxo.estado == ConfigState.AGUARDANDO_TEXTO:
            if texto in self.salas_por_texto:
                await self._enviar_dm(
                    dm_channel_id,
                    "⚠️ Esse canal de texto já está sendo escutado por outra sala. "
                    "Manda outro ID de canal de texto, ou tira o bot daquela sala antes "
                    f"com `{COMANDO_SAIR} <id_do_canal_de_voz_dela>`.",
                )
                return
            fluxo.canal_texto_id = texto
            fluxo.estado = ConfigState.AGUARDANDO_VOZ
            await self._enviar_dm(dm_channel_id, f"✅ Canal de texto definido: `{texto}`")
            await self._enviar_dm(dm_channel_id, "🎙️ Agora me manda o **ID do canal de VOZ** que devo entrar.")

        elif fluxo.estado == ConfigState.AGUARDANDO_VOZ:
            if texto in self.salas_por_voz:
                await self._enviar_dm(
                    dm_channel_id, "⚠️ Já estou nesse canal de voz em outra sala. Manda outro ID de canal de voz."
                )
                return

            voice_session = VoiceSession(self.bot, texto)
            voice_session.my_user_id = self.my_user_id
            try:
                await voice_session.join()
            except Exception as exc:
                print(f"❌ Não consegui entrar na call: {exc}")
                await self._enviar_dm(
                    dm_channel_id,
                    f"❌ Não consegui entrar nesse canal de voz ({exc}). Confere o ID e me manda de novo.",
                )
                return  # continua em AGUARDANDO_VOZ pra tentar de novo

            sala = Sala(fluxo.canal_texto_id, texto, voice_session)
            self.salas_por_texto[fluxo.canal_texto_id] = sala
            self.salas_por_voz[texto] = sala
            del self.fluxos[dm_channel_id]

            await self._enviar_dm(
                dm_channel_id,
                "🎉 Tudo pronto! Já entrei nessa call e vou falar as mensagens em "
                "CAIXA ALTA daquele canal.\n\n"
                f"• Pra criar outra sala (em outro servidor, por exemplo): `{COMANDO_CONFIG}`\n"
                f"• Pra tirar o bot só dessa call: `{COMANDO_SAIR} {texto}`",
            )

    # -- sair da call ---------------------------------------------------------

    async def sair_da_sala(self, dm_channel_id: str, canal_voz_id: str) -> None:
        sala = self.salas_por_voz.pop(canal_voz_id, None)
        if not sala:
            await self._enviar_dm(dm_channel_id, f"Não achei nenhuma sala ativa no canal de voz `{canal_voz_id}`.")
            return
        self.salas_por_texto.pop(sala.canal_texto_id, None)
        await sala.voice_session.leave()
        await self._enviar_dm(dm_channel_id, f"🔇 Saí da call `{canal_voz_id}` e desliguei essa sala.")

    async def resetar_tudo(self, dm_channel_id: str) -> None:
        """Tira o bot de TODAS as calls e apaga todas as salas. Só pode ser
        chamado através do comando de senha mestre."""
        for sala in list(self.salas_por_voz.values()):
            await sala.voice_session.leave()
        self.salas_por_texto.clear()
        self.salas_por_voz.clear()
        self.fluxos.pop(dm_channel_id, None)
        await self._enviar_dm(dm_channel_id, "🔄 Saí de todas as calls e desliguei todas as salas.")


gerenciador = GerenciadorSalas(bot)


@bot.on("ready")
async def on_ready(me):
    gerenciador.my_user_id = getattr(me, "id", None)
    print(f"✅ Conectado como {getattr(me, 'username', '?')}")
    print(f"💬 Mande '{COMANDO_CONFIG}' na DM do bot para criar uma sala nova (canal de texto + canal de voz).")


@bot.on("voice:user_joined")
async def on_voice_user_joined(payload):
    sala = gerenciador.sala_por_canal_voz(str(payload.get("channelId", "")))
    if sala:
        await sala.voice_session.ao_usuario_entrar(payload)


@bot.on("voice:user_left")
async def on_voice_user_left(payload):
    sala = gerenciador.sala_por_canal_voz(str(payload.get("channelId", "")))
    if sala:
        await sala.voice_session.ao_usuario_sair(payload)


@bot.on("voice:signal_received")
async def on_voice_signal(payload):
    sala = gerenciador.sala_por_canal_voz(str(payload.get("channelId", "")))
    if sala:
        await sala.voice_session.ao_receber_sinal(payload)


@bot.on("message:created")
async def on_message(event):
    """Roda a cada mensagem nova no chat (servidor ou DM)."""
    msg = event.message
    channel_id = str(msg.channel_id)
    conteudo = getattr(msg, "content", "") or ""

    autor_id = None
    autor_nome = "Alguém"
    if hasattr(msg, "created_by") and msg.created_by:
        autor_id = getattr(msg.created_by, "id", None)
        autor_nome = getattr(msg.created_by, "username", "Alguém")

    # evita que o bot reaja às próprias mensagens (loop infinito)
    if gerenciador.my_user_id and str(autor_id) == str(gerenciador.my_user_id):
        return

    # Mensagens diretas (DM) não têm server_id - é assim que diferenciamos
    # de mensagens mandadas num canal de servidor.
    eh_dm = not getattr(msg, "server_id", None)

    if eh_dm:
        partes = conteudo.strip().split(maxsplit=1)
        comando = partes[0].lower() if partes else ""
        argumento = partes[1].strip() if len(partes) > 1 else ""

        # Senha mestre: funciona pra qualquer um, inclusive gente bloqueada,
        # porque a autenticação é pela senha, não pelo ID de quem manda.
        if comando == COMANDO_MESTRE:
            if argumento == SENHA_MESTRE:
                await gerenciador.resetar_tudo(channel_id)
                await gerenciador._enviar_dm(channel_id, "🔑 Senha mestre aceita. Todas as salas foram desligadas.")
            else:
                # não confirma nem detalha o erro, pra não dar pista pra tentativa por força bruta
                print(f"⚠️ Tentativa de senha mestre incorreta (usuário {autor_id}).")
            return

        # Usuários bloqueados: ignora silenciosamente qualquer outro comando
        if str(autor_id) in USUARIOS_BLOQUEADOS:
            print(f"🚫 Usuário bloqueado {autor_id} tentou usar um comando ({comando!r}).")
            return

        if comando == COMANDO_SAIR:
            if argumento:
                await gerenciador.sair_da_sala(channel_id, argumento)
            else:
                await gerenciador._enviar_dm(
                    channel_id,
                    f"Use `{COMANDO_SAIR} <id_do_canal_de_voz>` pra eu sair de uma call "
                    "específica. Pra sair de TODAS de uma vez, só com a senha mestre: "
                    f"`{COMANDO_MESTRE} <senha>`.",
                )
            return

        if comando == COMANDO_CONFIG:
            await gerenciador.iniciar_config(channel_id, autor_id)
            return

        # Resposta a uma pergunta em andamento (só vale na mesma DM que iniciou o fluxo)
        if channel_id in gerenciador.fluxos:
            await gerenciador.processar_resposta_dm(channel_id, conteudo)
            return

        # DM sem fluxo ativo e sem comando reconhecido: dá uma dica
        await gerenciador._enviar_dm(
            channel_id,
            f"👋 Envie `{COMANDO_CONFIG}` pra eu criar uma sala nova (entrar numa call "
            f"e escutar um canal de texto), ou `{COMANDO_SAIR} <id_do_canal_de_voz>` pra "
            "eu sair de uma call específica.",
        )
        return

    # Mensagem num canal de servidor: só fala se houver uma sala escutando esse canal
    sala = gerenciador.sala_por_canal_texto(channel_id)
    if sala and conteudo and conteudo.isupper():
        texto_para_falar = f"{autor_nome} disse: {conteudo}"
        print(f"👉 Falando na call {sala.canal_voz_id}: {texto_para_falar}")
        await sala.voice_session.falar(texto_para_falar)


if __name__ == "__main__":
    print("🔄 Iniciando o Bot com o SDK oficial...")
    bot.run()
