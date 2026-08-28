import { Config } from "@remotion/cli/config";

// Fundo transparente: a camada é composta sobre o filme pelo FFmpeg no passo 5.
Config.setVideoImageFormat("png");
Config.setPixelFormat("yuva420p");
Config.setCodec("vp8");
