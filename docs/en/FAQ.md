# FAQ

[Русский](../ru/FAQ.md)

**Does EGO send my stories to the internet?**
No. The model runs on your computer and stories are stored locally. There is no outgoing traffic. The tunnel is off by default; if you turn it on, the traffic goes through Cloudflare.

**Which model do I need?**
A GGUF model that fits in your graphics card memory. The project was tested on an 8B model in Q8_0 quantisation on an RTX 3060 with 12 GB.

**Why does the reply stop in the middle?**
Usually the reply reached the length limit of the preset. Press "Continue", or choose a preset with a larger limit.

**Why does the model sometimes forget a fact?**
Small models lose details in long stories. The story memory helps, and important facts can be pinned with ★.

**Can I use EGO without a graphics card?**
Not in the first release. Only NVIDIA GPUs with CUDA are tested.

**Can several people use one installation?**
Yes: the owner adds accounts in the server window (Access tab). All accounts see the same stories and characters.

**How do I back up my stories?**
Stop Core and copy the `data` and `settings` folders. See the user guide.

**Is there content filtering?**
There are fixed limits on certain content, which apply to all stories. They are a safety net, not a guarantee. See the user guide.

**Why does a character refuse intimacy?**
The model plays the character as the card describes it. Without an age and a personality (like the template cards) the character is reserved and may turn a stranger down — that is the character, not an EGO rule. Give an age (of legal age) and describe the personality, e.g. «open to a romance with the user».

**The model repeats the same reply.**
Small models sometimes loop. EGO does not pass a repeated reply back to the model and adds a light repetition penalty. If a loop still happens, regenerate or write an action that changes the scene.

**English words appear in replies.**
That is the model. Model D (8B) often slips into English; Model A (12B) produced no English words in the test.
