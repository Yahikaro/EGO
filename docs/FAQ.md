# FAQ

[Русский](docs/FAQ.ru.md) — will be added later.

**Does EGO send my stories to the internet?**
No. The model runs on your computer and stories are stored locally. The only outgoing traffic is the sign-in email, and only if you enable sign-in from another device. The tunnel is off by default; if you turn it on, the traffic goes through Cloudflare.

**Which model do I need?**
A GGUF model that fits in your graphics card memory. The project was tested on an 8B model in Q8_0 quantisation on an RTX 3060 with 12 GB.

**Why does the reply stop in the middle?**
Usually the reply reached the length limit of the preset. Press "Continue", or choose a preset with a larger limit.

**Why does the model sometimes forget a fact?**
Small models lose details in long stories. The story memory helps, and important facts can be pinned with ★.

**Can I use EGO without a graphics card?**
Not in the first release. Only NVIDIA GPUs with CUDA are tested.

**Can several people use one installation?**
The first release supports one account per installation. Access for friends is planned for later.

**How do I back up my stories?**
Stop Core and copy the `data` and `settings` folders. See the user guide.

**Is there content filtering?**
There are fixed limits on certain content, which apply to all stories. They are a safety net, not a guarantee. See the user guide.
