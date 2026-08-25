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
import re
import time
from typing import Dict, Optional, Tuple

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
from aiortc.contrib.media import MediaPlayer, MediaRelay
from aiortc.sdp import candidate_from_sdp

from nerimity_sdk import Bot

# --- CONFIGURAÇÕES ---
TOKEN = "XXXXX"
VOZ = "pt-BR-AntonioNeural"  # Voz masculina padrão

# Comando pra sair da call. Uso: "!sair tts" (a palavra "tts" é fixa —
# tira o bot da sala que essa conversa (DM) criou, sem precisar saber o
# ID do canal de voz de cor).
COMANDO_SAIR = "!sair"
ARGUMENTO_SAIR = "tts"

# --- CONTROLE DE ACESSO ---
# IDs de usuários que NUNCA podem usar os comandos do bot.
# Adicione manualmente os IDs aqui, como texto, um por linha.
USUARIOS_BLOQUEADOS = {
    # "123456789012345678",
    # "987654321098765432",
}

# Comando de senha mestre: SEMPRE reseta TODAS as salas de uma vez (tira o
# bot de todas as calls), não importa quem manda - inclusive gente bloqueada
# - porque a autenticação aqui é pela senha, não pelo ID de quem enviou.
# É a ÚNICA forma de resetar tudo de uma vez; "!sair tts" só tira a sala
# criada por aquela conversa específica. Troque o valor abaixo antes de
# rodar o bot; use algo só seu.
# Uso: mande "!master SUA_SENHA_AQUI" na DM do bot.
COMANDO_MESTRE = "!master"
SENHA_MESTRE = "TROQUE_ESSA_SENHA"

SAMPLE_RATE = 48000
FRAME_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000  # 960 amostras por quadro

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

# Um único MediaRelay compartilhado por todo o processo. Ele existe pra
# resolver um problema específico: quando uma fala precisa ser ouvida por
# vários participantes ao mesmo tempo, cada um tem seu próprio
# RTCRtpSender/conexão, mas todos devem tocar o MESMO arquivo de fala.
# Se a gente desse o track de um único MediaPlayer direto pra vários
# senders, eles ficariam disputando (cada .recv() rouba um frame que o
# outro sender precisava) e o áudio saía cortado pra todo mundo. O relay
# resolve isso: cada sender recebe sua PRÓPRIA cópia (subscribe) do
# mesmo áudio de origem, sem brigar por frame.
_relay = MediaRelay()


class SilenceAudioTrack(MediaStreamTrack):
    """Faixa de áudio "de descanso": fica só emitindo silêncio, pra manter
    a conexão WebRTC viva e com uma faixa válida enquanto ninguém está
    falando. Uma instância é criada por participante (cada
    RTCPeerConnection precisa da sua própria).

    Quando o bot vai falar de verdade, a gente troca essa faixa pela do
    MediaPlayer via `sender.replaceTrack(...)` (ver VoiceSession._falar_um),
    e volta pra essa aqui quando a fala termina.
    """

    kind = "audio"

    def __init__(self):
        super().__init__()
        self._pts = 0
        self._start: Optional[float] = None
        self._silencio = b"\x00" * (SAMPLES_PER_FRAME * 2)

    async def recv(self):
        if self._start is None:
            self._start = time.monotonic()

        # mantém o ritmo de 20ms por quadro em tempo real
        target = self._start + (self._pts / SAMPLE_RATE)
        now = time.monotonic()
        delay = target - now

        # Se o event loop atrasou (outras conexões, sinalização etc.), não
        # tenta "correr atrás" mandando frames em rajada — como é silêncio,
        # só resincroniza o relógio suavemente. (Isso aqui já não afeta a
        # fala em si, que agora usa MediaPlayer — ver mais abaixo.)
        RESYNC_THRESHOLD = 0.08  # 80ms
        if delay < -RESYNC_THRESHOLD:
            self._start = now - (self._pts / SAMPLE_RATE)
        elif delay > 0:
            await asyncio.sleep(delay)

        samples = np.frombuffer(self._silencio, dtype=np.int16).reshape(1, -1)
        frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
        frame.sample_rate = SAMPLE_RATE
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, SAMPLE_RATE)
        self._pts += SAMPLES_PER_FRAME
        return frame


def _duracao_do_audio(caminho: str) -> float:
    """Descobre a duração (em segundos) do mp3 sem decodificar o áudio
    inteiro — só lê os metadados do container. Usado pra saber quanto
    tempo esperar antes de voltar a faixa pro silêncio depois de uma fala."""
    container = av.open(caminho)
    try:
        stream = container.streams.audio[0]
        if stream.duration is not None and stream.time_base is not None:
            return float(stream.duration * stream.time_base)
        if container.duration is not None:
            return container.duration / av.time_base
        return 0.0
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
        self.tracks: Dict[str, SilenceAudioTrack] = {}
        self._drain_tasks: Dict[str, list] = {}
        self._connected = False

        # Fila de falas pendentes: cada `falar()` só empilha o texto aqui;
        # quem realmente gera o TTS e toca é a `_processar_fila_de_fala`,
        # uma de cada vez, na ordem — assim várias mensagens em CAIXA ALTA
        # seguidas tocam em sequência, sem se sobrepor.
        self._fila_de_fala: "asyncio.Queue[Tuple[str, str]]" = asyncio.Queue()
        self._task_fala: Optional[asyncio.Task] = None

    # -- ciclo de vida --------------------------------------------------

    async def join(self) -> None:
        gateway = self.bot._gateway  # acesso "por baixo dos panos" do SDK
        socket_id = gateway.socket_id
        if not socket_id:
            raise RuntimeError("Gateway ainda não conectado; entre na voz depois do evento 'ready'.")

        await self.bot.rest.join_voice(self.channel_id, socket_id)
        self._connected = True
        self._task_fala = asyncio.create_task(self._processar_fila_de_fala())
        print(f"🔊 Entrou no canal de voz {self.channel_id}")

    async def leave(self) -> None:
        if self._task_fala:
            self._task_fala.cancel()
            self._task_fala = None
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
        track = SilenceAudioTrack()
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
        """Enfileira o texto pra ser falado. Quem realmente gera o áudio e
        toca é a `_processar_fila_de_fala`, então isso aqui só empilha e
        volta na hora — não trava esperando o TTS terminar."""
        await self._fila_de_fala.put((texto, voz))

    async def _processar_fila_de_fala(self) -> None:
        """Roda em background durante toda a vida da sessão: pega uma fala
        da fila, toca até o fim (esperando o tempo dela), só então pega a
        próxima. Isso garante que falas não se sobrepõem/misturam."""
        while True:
            texto, voz = await self._fila_de_fala.get()
            try:
                await self._falar_um(texto, voz)
            except Exception as exc:
                print(f"⚠️ Erro ao tocar fala, ignorando e seguindo pra próxima: {exc}")
            finally:
                self._fila_de_fala.task_done()

    async def _falar_um(self, texto: str, voz: str) -> None:
        if not self.tracks:
            print("⚠️ Ninguém conectado na call ainda (ou negociação em andamento); nada pra ouvir.")
            return

        out_file = f"tts_temp_{int(time.time() * 1000)}.mp3"
        try:
            communicate = edge_tts.Communicate(texto, voz)
            await communicate.save(out_file)

            if not os.path.exists(out_file) or os.path.getsize(out_file) == 0:
                print("⚠️ edge-tts gerou um arquivo vazio (provável falha de rede); ignorando essa fala.")
                return

            try:
                duracao = await asyncio.to_thread(_duracao_do_audio, out_file)
            except Exception as exc:
                print(f"⚠️ mp3 do TTS corrompido/incompleto, ignorando essa fala: {exc}")
                return

            # MediaPlayer abre o arquivo e cuida da decodificação/pacing em
            # cima do libav, numa thread própria — não compete com o loop
            # de eventos do bot (sinalização WebRTC de outras conexões,
            # heartbeat etc.) como a fila manual antiga competia. É isso
            # que resolve o "pipocar": o ritmo dos frames deixa de depender
            # de o loop asyncio estar livre no milissegundo certo.
            player = MediaPlayer(out_file)
            if player.audio is None:
                print("⚠️ MediaPlayer não encontrou faixa de áudio no mp3, ignorando essa fala.")
                return

            # Troca a faixa de cada participante pela faixa da fala (cada um
            # recebe sua PRÓPRIA cópia via relay, pra não brigarem por frame
            # entre si). Guarda quem foi trocado pra saber quem reverter
            # pro silêncio depois.
            trocados: list = []
            for user_id, pc in list(self.peers.items()):
                for sender in pc.getSenders():
                    if sender.track is not None and sender.track.kind == "audio":
                        relayed = _relay.subscribe(player.audio)
                        sender.replaceTrack(relayed)
                        trocados.append((user_id, sender))
                        break

            try:
                # espera a duração real da fala (+ uma folga) antes de
                # voltar pro silêncio, senão cortaria o áudio no meio.
                await asyncio.sleep(duracao + 0.3)
            finally:
                for user_id, sender in trocados:
                    silencio = self.tracks.get(user_id)
                    if silencio is not None:
                        sender.replaceTrack(silencio)
                player.audio.stop()
        finally:
            if os.path.exists(out_file):
                os.remove(out_file)


class Sala:
    """Uma sala = um par (canal de texto que o bot escuta) + (canal de voz
    onde ele fala). O bot pode ter várias salas ativas ao mesmo tempo, cada
    uma com sua própria VoiceSession — inclusive em servidores diferentes."""

    def __init__(self, canal_texto_id: str, canal_voz_id: str, voice_session: "VoiceSession"):
        self.canal_texto_id = canal_texto_id
        self.canal_voz_id = canal_voz_id
        self.voice_session = voice_session


# --- MODELO QUE A PESSOA PREENCHE NO PRIVADO -------------------------------
# Em vez de um fluxo de pergunta-resposta (!config -> pergunta canal de
# texto -> espera resposta -> pergunta canal de voz -> espera resposta),
# o bot manda um modelo pronto. A pessoa copia, preenche os dois IDs e
# manda de volta numa única mensagem; o bot lê e já entra na call.
_LABEL_TEXTO = "ID do canal de texto"
_LABEL_VOZ = "ID do canal de voz"

MODELO_MENSAGEM = (
    "👋 Pra eu entrar numa call, copia o texto abaixo, preenche os dois IDs "
    "(um em cada linha, depois dos dois-pontos) e manda de volta pra mim "
    "numa única mensagem:\n\n"
    "```\n"
    f"{_LABEL_TEXTO}: \n"
    f"{_LABEL_VOZ}: \n"
    "```\n\n"
    f"Pra eu sair da call depois, manda `{COMANDO_SAIR} {ARGUMENTO_SAIR}` "
    "no canal de TEXTO que você definiu (não aqui no privado)."
)

_PADRAO_TEXTO = re.compile(rf"(?im)^\s*{re.escape(_LABEL_TEXTO)}\s*:\s*(\S+)\s*$")
_PADRAO_VOZ = re.compile(rf"(?im)^\s*{re.escape(_LABEL_VOZ)}\s*:\s*(\S+)\s*$")


def extrair_ids_do_modelo(texto: str) -> Optional[Tuple[str, str]]:
    """Tenta ler uma mensagem no formato do MODELO_MENSAGEM (as duas linhas
    'ID do canal de texto: ...' e 'ID do canal de voz: ...') e devolve
    (id_texto, id_voz) se as duas vierem preenchidas. Devolve None se a
    mensagem não bater com esse formato (então não é uma resposta ao
    modelo, é só uma mensagem qualquer)."""
    m_texto = _PADRAO_TEXTO.search(texto or "")
    m_voz = _PADRAO_VOZ.search(texto or "")
    if m_texto and m_voz:
        return m_texto.group(1).strip(), m_voz.group(1).strip()
    return None


class GerenciadorSalas:
    """Gerencia todas as salas (calls) ativas do bot.

    No PRIVADO do bot (por qualquer pessoa):
      <qualquer mensagem que não seja o modelo preenchido> -> manda o
                                                                modelo pra
                                                                preencher
      <modelo preenchido com os dois IDs>                   -> já entra na
                                                                call
      !master <senha>                                        -> reseta
                                                                TODAS as
                                                                salas

    No canal de TEXTO que a sala está escutando (não no privado):
      !sair tts -> tira o bot da call e desliga aquela sala
    """

    def __init__(self, bot: Bot):
        self.bot = bot
        self.my_user_id: Optional[str] = None
        self.salas_por_texto: Dict[str, Sala] = {}   # canal_texto_id -> Sala
        self.salas_por_voz: Dict[str, Sala] = {}      # canal_voz_id  -> Sala
        self.salas_por_dm: Dict[str, Sala] = {}       # dm_channel_id -> Sala (quem criou)

    async def _enviar_dm(self, channel_id: str, texto: str) -> None:
        if channel_id:
            await self.bot.rest.create_message(channel_id, texto)

    def sala_por_canal_texto(self, canal_texto_id: str) -> Optional[Sala]:
        return self.salas_por_texto.get(canal_texto_id)

    def sala_por_canal_voz(self, canal_voz_id: str) -> Optional[Sala]:
        return self.salas_por_voz.get(canal_voz_id)

    # -- criar sala a partir do modelo preenchido ----------------------------

    async def criar_sala(self, dm_channel_id: str, canal_texto_id: str, canal_voz_id: str) -> None:
        if dm_channel_id in self.salas_por_dm:
            await self._enviar_dm(
                dm_channel_id,
                "⚠️ Você já tem uma sala ativa criada por aqui. Vá até o canal de texto "
                f"dela e manda `{COMANDO_SAIR} {ARGUMENTO_SAIR}` antes de criar outra.",
            )
            return

        if canal_texto_id in self.salas_por_texto:
            await self._enviar_dm(
                dm_channel_id,
                "⚠️ Esse canal de texto já está sendo escutado por outra sala. "
                "Preenche o modelo com outro ID de canal de texto.",
            )
            return

        if canal_voz_id in self.salas_por_voz:
            await self._enviar_dm(
                dm_channel_id,
                "⚠️ Já estou nesse canal de voz em outra sala. Preenche o modelo "
                "com outro ID de canal de voz.",
            )
            return

        voice_session = VoiceSession(self.bot, canal_voz_id)
        voice_session.my_user_id = self.my_user_id
        try:
            await voice_session.join()
        except Exception as exc:
            print(f"❌ Não consegui entrar na call: {exc}")
            await self._enviar_dm(
                dm_channel_id,
                f"❌ Não consegui entrar nesse canal de voz ({exc}). Confere os IDs "
                "e manda o modelo preenchido de novo.",
            )
            return

        sala = Sala(canal_texto_id, canal_voz_id, voice_session)
        self.salas_por_texto[canal_texto_id] = sala
        self.salas_por_voz[canal_voz_id] = sala
        self.salas_por_dm[dm_channel_id] = sala

        await self._enviar_dm(
            dm_channel_id,
            "🎉 Tudo pronto! Já entrei nessa call e vou falar as mensagens em "
            "CAIXA ALTA daquele canal.\n\n"
            f"Pra eu sair, manda `{COMANDO_SAIR} {ARGUMENTO_SAIR}` lá no canal "
            "de texto (não aqui no privado).",
        )

    # -- sair da call ---------------------------------------------------------

    async def sair_da_sala(self, canal_texto_id: str) -> None:
        """Tira o bot da call escutada por esse canal de texto. Precisa ser
        chamado com o ID do canal de texto onde o comando foi mandado —
        é assim que o bot sabe qual sala desligar."""
        sala = self.salas_por_texto.pop(canal_texto_id, None)
        if not sala:
            return  # nenhuma sala ativa nesse canal; nada a fazer
        self.salas_por_voz.pop(sala.canal_voz_id, None)
        for dm_channel_id, s in list(self.salas_por_dm.items()):
            if s is sala:
                del self.salas_por_dm[dm_channel_id]
        await sala.voice_session.leave()
        await self._enviar_dm(canal_texto_id, "🔇 Saí da call e desliguei essa sala.")

    async def resetar_tudo(self, dm_channel_id: str) -> None:
        """Tira o bot de TODAS as calls e apaga todas as salas. Só pode ser
        chamado através do comando de senha mestre."""
        for sala in list(self.salas_por_voz.values()):
            await sala.voice_session.leave()
        self.salas_por_texto.clear()
        self.salas_por_voz.clear()
        self.salas_por_dm.clear()
        await self._enviar_dm(dm_channel_id, "🔄 Saí de todas as calls e desliguei todas as salas.")


gerenciador = GerenciadorSalas(bot)


@bot.on("ready")
async def on_ready(me):
    gerenciador.my_user_id = getattr(me, "id", None)
    print(f"✅ Conectado como {getattr(me, 'username', '?')}")
    print("💬 Mande qualquer mensagem na DM do bot pra receber o modelo de configuração.")


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
        conteudo_stripped = conteudo.strip()
        partes = conteudo_stripped.split(maxsplit=1)
        comando = partes[0].lower() if partes else ""
        argumento = partes[1].strip() if len(partes) > 1 else ""

        # Senha mestre: funciona pra qualquer um, inclusive gente bloqueada,
        # porque a autenticação é pela senha, não pelo ID de quem manda.
        if comando == COMANDO_MESTRE:
            if argumento == SENHA_MESTRE:
                await gerenciador.resetar_tudo(channel_id)
            else:
                # não confirma nem detalha o erro, pra não dar pista pra tentativa por força bruta
                print(f"⚠️ Tentativa de senha mestre incorreta (usuário {autor_id}).")
            return

        # Usuários bloqueados: ignora silenciosamente qualquer outro comando
        if str(autor_id) in USUARIOS_BLOQUEADOS:
            print(f"🚫 Usuário bloqueado {autor_id} tentou mandar uma mensagem ({conteudo_stripped!r}).")
            return

        # A mensagem é o modelo preenchido com os dois IDs? Se sim, já cria
        # a sala direto - não precisa de nenhum comando antes.
        ids = extrair_ids_do_modelo(conteudo_stripped)
        if ids:
            canal_texto_id, canal_voz_id = ids
            await gerenciador.criar_sala(channel_id, canal_texto_id, canal_voz_id)
            return

        # Qualquer outra mensagem na DM: manda o modelo pra pessoa preencher.
        await gerenciador._enviar_dm(channel_id, MODELO_MENSAGEM)
        return

    # Mensagem num canal de servidor: só faz algo se houver uma sala escutando esse canal
    sala = gerenciador.sala_por_canal_texto(channel_id)
    if not sala:
        return

    conteudo_stripped = conteudo.strip()

    # "!sair tts" só funciona aqui, no canal de texto que a sala escuta —
    # não mais no privado do bot.
    if conteudo_stripped.lower() == f"{COMANDO_SAIR} {ARGUMENTO_SAIR}":
        if str(autor_id) in USUARIOS_BLOQUEADOS:
            print(f"🚫 Usuário bloqueado {autor_id} tentou usar {COMANDO_SAIR} {ARGUMENTO_SAIR}.")
            return
        await gerenciador.sair_da_sala(channel_id)
        return

    if conteudo_stripped and conteudo_stripped.isupper():
        texto_para_falar = f"{autor_nome} disse: {conteudo_stripped}"
        print(f"👉 Falando na call {sala.canal_voz_id}: {texto_para_falar}")
        await sala.voice_session.falar(texto_para_falar)


if __name__ == "__main__":
    print("🔄 Iniciando o Bot com o SDK oficial...")
    bot.run()
