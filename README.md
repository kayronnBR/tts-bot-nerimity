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
