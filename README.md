
# 🤖 Nerimity TTS Voice Bot

Bot para o [Nerimity](https://nerimity.com) que **entra automaticamente em um canal de voz** e **fala em voz alta** (usando TTS da Microsoft/Edge) qualquer mensagem escrita **EM CAPS LOCK** em um canal de texto.

O áudio é transmitido **de verdade para dentro da chamada de voz** via WebRTC (não é só tocar um som no computador que roda o bot) — outras pessoas na call ouvem o bot falando.

---

## ⚠️ Aviso importante

A Nerimity não possui uma API oficial e documentada para "bot entra na call e manda áudio". As chamadas de voz funcionam com **WebRTC em malha (mesh)**, sinalizado por Socket.IO. Este bot foi construído fazendo engenharia reversa do `nerimity_sdk` e do [cliente web oficial da Nerimity](https://github.com/Nerimity/nerimity-web) para replicar esse comportamento.

Ou seja: funciona hoje, mas **não é um recurso oficialmente suportado**. Se a Nerimity mudar o protocolo de sinalização no futuro, pode ser necessário ajustar o código.

---

## ✨ Funcionalidades

- Conecta ao Nerimity usando o [`nerimity_sdk`](https://pypi.org/project/nerimity_sdk/) oficial.
- Entra automaticamente em um canal de voz configurado.
- Escuta um canal de texto e detecta mensagens 100% em **CAIXA ALTA**.
- Gera a fala com [`edge-tts`](https://github.com/rany2/edge-tts) (voz neural da Microsoft, gratuita).
- Transmite o áudio ao vivo para todos os participantes conectados na chamada via WebRTC (`aiortc`).

---

## 📋 Pré-requisitos

- Python **3.9 ou superior**
- Uma conta/token de bot no Nerimity ([como criar um bot](https://docs.nerimity.com))
- Acesso à internet (para os servidores STUN/TURN e para gerar o TTS)
- (Opcional, mas recomendado) `ffmpeg` instalado no sistema

---

## 🚀 Instalação — passo a passo

### 1. Crie um ambiente virtual (recomendado)

```bash
python3 -m venv venv

```

### 2. Instale as dependências

```bash
~/venv/bin/pip install nerimity_sdk aiortc av numpy edge-tts

```

Se preferir instalar manualmente, sem o `requirements.txt`:

```bash
pip install nerimity_sdk aiortc av numpy edge-tts

```

> 💡 O `aiortc` e o `av` já trazem o `ffmpeg` embutido para decodificar áudio, então normalmente não é preciso instalar nada a mais no sistema. Se aparecer algum erro relacionado a `libav`/`ffmpeg` faltando, instale o ffmpeg do seu sistema:
> ```bash
> # Ubuntu/Debian
> sudo apt install ffmpeg
> 
> # macOS (Homebrew)
> brew install ffmpeg
> 
> ```
> 
> 

### 3. Pegue o token do seu bot

1. Acesse as configurações de desenvolvedor da sua conta na Nerimity: `https://nerimity.com/app/settings/developer`
2. Crie um bot e copie o **token**.
3. Convide o bot para o seu servidor com permissão para ler mensagens e entrar em canais de voz.

### 4. Pegue os IDs dos canais

No app da Nerimity, ative o **modo desenvolvedor** (se disponível) ou copie o ID pela URL do canal ao clicar nele. Você vai precisar de:

* ID do **canal de texto** que o bot vai "escutar".
* ID do **canal de voz** que o bot vai entrar.

### 5. Configure o `bot.py`

Abra o arquivo `bot.py` e edite o topo com suas informações:

```python
TOKEN = "SEU_TOKEN_AQUI"
CANAL_TEXTO_ID = "ID_DO_CANAL_DE_TEXTO"
CANAL_VOZ_ID = "ID_DO_CANAL_DE_VOZ"
VOZ = "pt-BR-AntonioNeural"  # voz do TTS (veja a lista abaixo)

```

### 6. Rode o bot

```bash
python bot.py

```

Se tudo der certo, você verá algo como:

```
🔄 Iniciando o Bot com o SDK oficial...
✅ Conectado como SeuBot
🔊 Entrou no canal de voz ...

```

Agora é só mandar uma mensagem **EM CAPS LOCK** no canal de texto configurado — o bot vai gerar o áudio e falar na call.

---

## 📦 O que tem no código

O arquivo `bot.py` atualizado traz uma arquitetura robusta voltada para alta disponibilidade, segurança, gerenciamento dinâmico de múltiplas salas e otimização extrema de memória RAM:

1. **Configurações e Parâmetros da Chamada (`TOKEN`, `VOZ`, `ICE_SERVERS`)**:
* Armazena o token do bot, a voz do Microsoft Edge TTS (`pt-BR-AntonioNeural`) e os servidores STUN/TURN oficiais extraídos do cliente web da Nerimity para negociação ICE de conexões WebRTC.


2. **Sistema de Controle de Acesso e Bloqueio (`USUARIOS_BLOQUEADOS`)**:
* Um conjunto (`set`) contendo IDs de usuários explicitamente impedidos de executar comandos (`!config`, `!sair`) nas mensagens diretas (DM) do bot.


3. **Autenticação por Senha Mestre (`COMANDO_MESTRE`, `SENHA_MESTRE`)**:
* Permite executar o comando `!master <senha>` via DM para desconectar o bot de todas as chamadas e resetar todas as salas ativas instantaneamente. Por ser autenticado via senha, funciona inclusive se enviado por usuários da lista de bloqueio.


4. **Tratamento de Áudio com Prevenção de Inundação (`TTSAudioTrack`)**:
* Subclasse de `MediaStreamTrack` do `aiortc`. Mantém um fluxo contínuo de pacotes em tempo real (20ms/frame) enviando silêncio até que o áudio PCM do TTS seja injetado.
* Possui uma trava de segurança baseada na constante `MAX_QUEUE_FRAMES`: se uma conexão P2P travar e parar de consumir frames, os áudios antigos são descartados da fila em vez de acumularem na RAM.


5. **Gerenciador de Conexão e Descarte Ativo de Áudio Recebido (`VoiceSession`)**:
* Gerencia a presença e negociação SDP/ICE em um canal de voz.
* **Consumo Ativo de Microfone**: Em redes WebRTC mesh, todos os participantes transmitem áudio para todos. Como o `aiortc` empilha áudios recebidos dos usuários numa fila sem limite (`RemoteStreamTrack._queue`), o bot roda a task `_descartar_audio_recebido` para ler e descartar continuamente todo o áudio dos outros participantes em segundo plano, impedindo vazamentos de memória por acúmulo de áudio.
* **Watchdog de Conexão Zumbi**: A task `_watchdog_conexao` monitora conexões travadas nos estados `"connecting"` ou `"new"`. Se não estabelecerem comunicação em 20 segundos (`PEER_CONNECT_TIMEOUT`), a conexão é fechada e descartada automaticamente.


6. **Decodificação Segura com Liberação de Recursos (`decodificar_mp3_para_pcm`)**:
* Converte o arquivo MP3 gerado em PCM mono 16-bit 48kHz via PyAV/FFmpeg.
* Utiliza bloco `try/finally` obrigatório para garantir o fechamento do container do FFmpeg (`container.close()`) mesmo que o arquivo venha corrompido ou incompleto (ex.: falha de pacote do `edge-tts` que gerava o erro `"packet queue is empty, aborting"`), impedindo o vazamento de descritores de arquivo.
* A decodificação é executada em uma thread assíncrona separada (`asyncio.to_thread`) para evitar o bloqueio do loop de eventos principal do bot durante o processamento de CPU/IO.


7. **Arquitetura Multi-Sala Assíncrona (`Sala`, `FluxoConfig`, `GerenciadorSalas`)**:
* Permite que o bot esteja presente e ativo em múltiplas salas (duplas de canal de texto e voz) simultaneamente.
* Gerencia estados de conversação interativa via DM (`ConfigState`) para criar novas salas passo a passo de forma isolada por usuário.



---

## ⚙️ Como o bot funciona (Fluxo de Execução Técnica)

Abaixo está o ciclo de vida operacional detalhado do bot, desde a recepção de comandos até o envio do fluxo de voz WebRTC:

```
                  [ Evento 'message:created' ]
                               │
               ┌───────────────┴───────────────┐
               ▼                               ▼
        [ Mensagem em DM ]            [ Mensagem no Servidor ]
               │                               │
       ┌───────┴───────┐              [ Pertence a uma Sala Ativa? ]
       ▼               ▼                       │
 [ Comando !master ] [ Outros Comandos ]       ▼
 [ Valida Senha ]   [ Checa Bloqueio ]  [ Texto em CAPS LOCK? ]
       │               │                       │
       ▼               ▼                       ▼
 [ Reseta Salas ]   [!config / !sair]   [ Inicia Geração de TTS ]

```

### Detalhamento dos Processos:

1. **Configuração Dinâmica de Salas via DM**:
* O usuário envia `!config` na DM do bot.
* O `GerenciadorSalas` inicia um `FluxoConfig` no estado `AGUARDANDO_TEXTO` e solicita o ID do canal de texto.
* Após o recebimento e validação, o estado passa para `AGUARDANDO_VOZ` e solicita o ID do canal de voz.
* O bot aciona `bot.rest.join_voice()` para registrar presença no canal e cria a `VoiceSession`.


2. **Negociação WebRTC Mesh**:
* Quando um participante entra no canal de voz, o evento `voice:user_joined` dispara `ao_usuario_entrar`.
* O bot cria uma oferta SDP via `RTCPeerConnection` (`aiortc`), envia via socket `voice:signal_send` e aguarda a resposta SDP e candidatos ICE via `voice:signal_received`.
* Assim que estabelecida a conexão, o bot associa um `TTSAudioTrack` para transmissão e inicia o loop assíncrono para descartar o áudio que o usuário transmite na call.


3. **Ciclo de Processamento da Fala (TTS)**:
* Uma mensagem em CAIXA ALTA (`isupper()`) postada no canal de texto cadastrado aciona a função `falar()`.
* O bot gera o áudio MP3 utilizando `edge_tts.Communicate`.
* O arquivo é validado (verificando se o tamanho é maior que 0 bytes para prevenir erros de rede/rate limit).
* O áudio é decodificado via `decodificar_mp3_para_pcm` em thread separada, convertendo para PCM bruto de 48kHz.
* O PCM é fatiado em blocos equivalentes a 20ms de áudio (`SAMPLES_PER_FRAME = 960` amostras) e inserido via `push_pcm()` na fila de cada participante ativo na chamada.
* O arquivo MP3 temporário é excluído do disco no bloco `finally`.


4. **Gerenciamento de Comandos Disponíveis via DM**:
* `!config`: Inicia o assistente de criação de uma nova sala (canal de texto + canal de voz).
* `!sair <id_canal_voz>`: Remove o bot de uma chamada de voz específica e encerra a sala correspondente.
* `!master <senha>`: Comando mestre global que força a desconexão de todas as chamadas e o encerramento de todas as salas cadastradas.



---

## 🎙️ Trocando a voz do TTS

O `edge-tts` suporta várias vozes em português e outros idiomas. Para listar todas as vozes disponíveis:

```bash
edge-tts --list-voices | grep pt-BR

```

Exemplos de vozes em português do Brasil:

| Voz | Gênero |
| --- | --- |
| `pt-BR-AntonioNeural` | Masculina |
| `pt-BR-FranciscaNeural` | Feminina |

Basta trocar o valor de `VOZ` no topo do `bot.py`.

---

## 🗂️ Estrutura do projeto

```
.
├── bot.py             # código principal do bot
├── requirements.txt   # dependências Python
└── README.md          # esta documentação

```

---

## 🔧 Como funciona (visão geral técnica)

1. O bot conecta ao gateway (WebSocket) da Nerimity usando o `nerimity_sdk`.
2. Assim que fica pronto (`evento ready`), chama o endpoint REST `POST /channels/{id}/voice/join` para entrar no canal de voz.
3. Para cada participante já presente ou que entra depois, o bot negocia uma conexão **WebRTC ponto a ponto** (oferta/resposta SDP + candidatos ICE), trocando essas mensagens pelos eventos de socket `voice:signal_send` / `voice:signal_received` — exatamente como o cliente web oficial faz internamente com a biblioteca `simple-peer`.
4. Quando uma mensagem em CAIXA ALTA chega no canal de texto configurado, o bot gera o áudio com `edge-tts`, decodifica para PCM com `av` (PyAV) e envia esse áudio por dentro de cada conexão WebRTC ativa, usando `aiortc`.

---

## ❓ Solução de problemas

**`ModuleNotFoundError: No module named 'av'` (ou `aiortc`, `edge_tts`, `nerimity_sdk`)**
→ As dependências não foram instaladas no ambiente Python que você está usando para rodar o bot. Rode `pip install -r requirements.txt` dentro do mesmo venv/interpretador usado para executar o `bot.py`.

**O bot entra na call mas ninguém ouve nada**
→ Verifique no console as linhas `[RTC] <usuario>: <estado>`. Se o estado nunca chega a `connected`, pode ser um problema de conectividade ICE/TURN (rede com firewall restritivo, por exemplo).

**O bot lê a própria mensagem e entra em loop**
→ Confirme que o token configurado é o do bot correto e que o evento `ready` está disparando (deve aparecer `✅ Conectado como ...` no console).

**Erro relacionado a `ffmpeg`/`libav**`
→ Instale o ffmpeg no sistema operacional (veja o passo 3 acima).

---
