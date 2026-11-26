<!--
Draft for LiveKit's docs team, following the structure of the existing TTS provider pages.
Things to confirm before publishing:
- The install line assumes the plugin ships in the next 1.9.x release.
- Node.js is listed because livekit/agents-js#2958 is open; drop it if only Python merges first.
- The voice counts are left out on purpose; the voices page has the current list.
-->

# Vartaloop TTS plugin guide

> How to use the Vartaloop TTS plugin for LiveKit Agents.

Available in:
- [x] Node.js
- [x] Python

## Overview

This plugin allows you to use [Vartaloop](https://vartaloop.ai/) as a TTS provider for your voice agents. Vartaloop provides streaming speech synthesis for Hinglish (Hindi and English mixed in one sentence) and Indian English, with a server-side text normaliser that reads amounts, dates, phone numbers and OTPs the way Indian callers expect.

### Installation

Install the plugin from PyPI:

```shell
uv add "livekit-agents[vartaloop]~=1.9"
```

Or from npm:

```shell
pnpm add @livekit/agents-plugin-vartaloop
```

### Authentication

The Vartaloop plugin requires a [Vartaloop API key](https://tts.vartaloop.ai/).

Set `VARTALOOP_API_KEY` in your `.env` file:

```shell
VARTALOOP_API_KEY=<your-vartaloop-api-key>
```

### Usage

Use Vartaloop TTS within an `AgentSession` or as a standalone speech generator. For example, you can use this TTS in the [Voice AI quickstart](https://docs.livekit.io/agents/start/voice-ai.md).

**Python**

```python
from livekit.agents import AgentSession
from livekit.plugins import vartaloop

session = AgentSession(
   tts=vartaloop.TTS(
      voice="saanvi",
      expression="neutral",
      digits="auto",
   ),
   # ... llm, stt, etc.
)
```

**Node.js**

```typescript
import { voice } from '@livekit/agents';
import * as vartaloop from '@livekit/agents-plugin-vartaloop';

const session = new voice.AgentSession({
  tts: new vartaloop.TTS({
    voice: 'saanvi',
    expression: 'neutral',
    digits: 'auto',
  }),
  // ... llm, stt, etc.
});
```

Realtime streaming through `stream()` uses Vartaloop's WebSocket API. The plugin sentence-tokenizes LLM output and sends each sentence as its own utterance, so the first sentence plays while the LLM is still writing the next. One-shot synthesis through `synthesize()` uses `POST /v1/tts`. WebSocket connections are pooled and reused across agent turns. On interruption, the plugin closes the connection so the server stops synthesizing, and the next turn opens a new one.

### Voices

Vartaloop voices are identified by an ID such as `saanvi`. The voice decides the language and the text normaliser, so send Hinglish text to a Hinglish voice and English text to an English voice. List the available voices with the [voices API](https://docs.vartaloop.ai/voices/) (`GET /v1/voices`), which also returns the expressions each voice supports.

### Parameters

This section describes some of the available parameters. See the [plugin reference](https://docs.livekit.io/reference/python/livekit/plugins/vartaloop/index.html.md#livekit.plugins.vartaloop.TTS) for a complete list of all available parameters. Node.js uses the same names in camelCase.

- **`voice`** _(string)_ (optional) - Default: `saanvi`: Voice ID to use for synthesis. See [Voices](#voices).

- **`model`** _(string)_ (optional) - Default: `narad`: The Vartaloop model family. Currently `narad`.

- **`expression`** _(string)_ (optional) - Default: `neutral`: Delivery of the voice. One of `neutral`, `excited`, `sad` or `whisper`. A voice without the requested expression plays neutral.

- **`digits`** _(string)_ (optional) - Default: `auto`: How digits are read in Hinglish text. `hi` reads Hindi number words, `mixed` reads codes, OTPs and phone numbers as English digit names and amounts in Hindi, `en` reads English number words with Indian grouping, and `auto` picks `en` when the text has Latin words and `hi` otherwise. English voices ignore it.

- **`speed`** _(float)_ (optional) - Default: `None`: Speaking pace from `0.5` to `2.0`. The pitch stays the same. Unset plays the voice's natural pace.

- **`profile`** _(string)_ (optional) - Default: `None`: Output chain profile. `telephony` shapes the audio for 8 kHz μ-law delivery over SIP and PSTN.

- **`temperature`** _(float)_ (optional) - Default: `None`: Sampling noise from `0` to `2`. The server default is `0.3`.

- **`seed`** _(integer)_ (optional) - Default: `None`: Fixes the sampling noise for reproducible output.

- **`raw`** _(boolean)_ (optional) - Default: `False`: Skip the server's text normaliser when the text is already normalised.

- **`sample_rate`** _(integer)_ (optional) - Default: `24000`: Output PCM sample rate in Hz. Must be `24000`.

- **`base_url`** _(string)_ (optional) - Default: `https://api.vartaloop.ai`: Vartaloop API base URL.

- **`tokenizer`** _(tokenize.SentenceTokenizer)_ (optional): Sentence tokenizer used to split LLM output for streaming synthesis. Defaults to LiveKit's basic sentence tokenizer, which also ends a sentence on the Devanagari danda (`।`).

Use `update_options()` (`updateOptions()` in Node.js) to change `voice`, `expression`, `digits`, `speed` or the other synthesis options mid-call. The change applies from the next sentence, without reconnecting.

## Additional resources

The following resources provide more information about using Vartaloop with LiveKit Agents.

- **[Python package](https://pypi.org/project/livekit-plugins-vartaloop/)**: The `livekit-plugins-vartaloop` package on PyPI.

- **[Node.js package](https://www.npmjs.com/package/@livekit/agents-plugin-vartaloop)**: The `@livekit/agents-plugin-vartaloop` package on npm.

- **[Plugin reference](https://docs.livekit.io/reference/python/livekit/plugins/vartaloop/index.html.md#livekit.plugins.vartaloop.TTS)**: Reference for the Vartaloop TTS plugin.

- **[GitHub repo](https://github.com/livekit/agents/tree/main/livekit-plugins/livekit-plugins-vartaloop)**: View the source or contribute to the LiveKit Vartaloop TTS plugin.

- **[Vartaloop docs](https://docs.vartaloop.ai/)**: Vartaloop's API reference, voices and text normalisation guide.

- **[Voice AI quickstart](https://docs.livekit.io/agents/start/voice-ai.md)**: Get started with LiveKit Agents and Vartaloop.
