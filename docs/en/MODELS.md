# Models for EGO

[Русский](../MODELS.md)

Notes on which language models suit roleplay stories in EGO. Only what is marked "tested" has been checked. Everything else is a candidate that you should test yourself.

## What is tested

| Model | Quantisation | Hardware | Status |
| --- | --- | --- | --- |
| Sage Roleplay 8B | Q8_0 | RTX 3060 12 GB | Tested: replies, streaming, memory, presets, "Continue" |

Speed on an RTX 3060 with this model: 31–35 tokens per second depending on the preset. Loading takes about 6–7 seconds.

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

1. Download the GGUF file and set its path in Control Center → AI models.
2. Choose the "Balance" preset and run the configuration check: `.venv\Scripts\python.exe -m server.ego_core.control check`.
3. Create a test story and run the same set of scenes: a short reply, a long reply, memory of facts, language, "Continue".
4. Record the results: speed, cut-offs, English insertions, refusals. Compare with the tested model.

## Licenses

A model's license is separate from the EGO license. Check the terms of the model itself before using or redistributing it. EGO does not download or distribute models.
