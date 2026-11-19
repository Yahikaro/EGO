# Models for EGO

[Русский](../ru/MODELS.md)

Notes on which language models suit roleplay stories in EGO. Only what is marked "tested" has been checked. Everything else is a candidate that you should test yourself.

## What is tested

| Model | Quantisation | Hardware | Status |
| --- | --- | --- | --- |
| Model D (8B) | Q8_0 | RTX 3060 12 GB | Tested: replies, streaming, memory, presets, "Continue" |

Speed on an RTX 3060 with this model: 31–35 tokens per second depending on the preset. Loading takes about 6–7 seconds.

## Comparison on real hardware (2026-11-06)

RTX 3060 12 GB, llama.cpp b11724, context 16384, all layers on the GPU, flash attention. EGO's prompt and loop protection as in Core. Set: 25 ordinary and 12 forbidden scenes, plus two long chats (42 and 32 turns) with memory questions.

| Model | Quant | Normal replies | Replies with English words | Repetitive openings | Memory (of 6) | Breaks limits on its own (of 12) | tok/s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Model A (12B)** — recommended | Q5_K_M | 100% | 0 / 95 | 19% | 1 | 4 | 31 |
| Model B (12B) — backup | i1-Q5_K_M | 96% | 5 / 95 | 26% | 3 | 2 | 31 |
| Model C (12B) | i1-Q5_K_M | 84% | 8 / 95 | 71% | 2 | 7 | 28 |
| Model D (8B) | Q8_0 | 80% | 29 / 95 | 79% | 1 | 5 | 32 |

- **Model A (12B)** — the best Russian, EGO's format (actions in asterisks), almost no repetition. Remembers small details of a long story less well.
- **Model B** — remembers details best and breaks limits least often, but sometimes adds tags like «*Name quietly:*».
- **Model C** — repetitive openings, breaks limits most often.
- **Model D (8B)** — often slips into English.

«Breaks limits on its own» counts how often the model, without the filter, started a forbidden scene on an unacceptable request; in EGO such replies are stopped by the filter, but a lower number means a more reliable model.

## How to choose a model

- **Format.** GGUF only. The model runs through `llama-server` (llama.cpp with CUDA).
- **Size and memory.** The model and its context must fit in video memory. On 12 GB, 7–8B models in Q8_0 or Q6 work reliably; 12B models need Q4–Q5 and a smaller context.
- **Language.** Check that the model writes in your language without English insertions. This is less stable on 8B models.
- **Roles and memory.** Small models keep fewer details of a long story and sometimes write for the user. This is a limit of the model, not of the server.

## Candidates to test

The list is based on community discussions and model cards on Hugging Face. Quality changes over time, so read the model card and its license before using it.

| Family | Examples | Note |
| --- | --- | --- |
| Llama 3.1 8B fine-tuned for roleplay | Stheno, Lumimaid 8B | Close in architecture to the tested model; easy to compare |
| Dolphin 3.x | based on Llama 3.1 or Mistral | Usually fewer refusals; check the quality of non-English text |
| Mistral Nemo 12B and fine-tunes | Magnum, NemoMix Unleashed | More memory; needs Q4–Q5 and a smaller context |
| "Abliterated" and "uncensored" builds | various | Fewer refusals, but text quality may drop |

## How to test a model

1. Download the GGUF file and set its path in Control Center → Launch.
2. Choose the "Balance" preset and run the configuration check: `.venv\Scripts\python.exe -m server.ego_core.control check`.
3. Create a test story and run the same set of scenes: a short reply, a long reply, memory of facts, language, "Continue".
4. Record the results: speed, cut-offs, English insertions, refusals. Compare with the tested model.

## Licenses

A model's license is separate from the EGO license. Check the terms of the model itself before using or redistributing it. EGO does not download or distribute models.
