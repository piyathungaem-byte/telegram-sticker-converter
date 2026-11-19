# Sticker Studio — Android App Project

Native Android client for converting Telegram sticker packs into WhatsApp-importable sticker packs.

## Architecture

This project is organized using standard Modern Android Architecture (MVVM + Jetpack Compose + Clean Architecture):

```
android-app/
├── app/
│   ├── build.gradle.kts
│   └── src/main/
│       ├── AndroidManifest.xml
│       ├── java/com/stickerstudio/app/
│       │   ├── MainActivity.kt
│       │   ├── StickerStudioApp.kt
│       │   ├── data/
│       │   │   ├── api/          # Retrofit / Telegram Bot API client
│       │   │   ├── model/        # Telegram JSON response DTOs
│       │   │   └── local/        # EncryptedSharedPreferences (bot token)
│       │   ├── domain/
│       │   │   ├── model/        # StickerItem, ConvertedSticker, WhatsAppPack
│       │   │   └── converter/    # StaticStickerConverter (512x512 WebP, tray icon)
│       │   ├── whatsapp/
│       │   │   ├── WhatsAppStickerProvider.kt   # Official WhatsApp ContentProvider
│       │   │   └── WhatsAppIntentHelper.kt     # "Add to WhatsApp" Intent launcher
│       │   ├── navigation/       # NavHost + Screen routes
│       │   └── ui/
│       │       ├── screens/      # Compose screens (Home, Browse, Select, etc.)
│       │       └── theme/        # Dark theme tokens matching the prototype
│       └── res/
├── build.gradle.kts
├── settings.gradle.kts
└── gradle.properties
```

## Core Modules & Capabilities

1. **Telegram API Client (`data/api`)**:
   - Fetches sticker pack metadata via `getStickerSet`.
   - Resolves asset download URLs via `getFile`.

2. **Secure Token Vault (`data/local/TokenStorage.kt`)**:
   - Encrypts user Telegram Bot Tokens inside the **Android Keystore** via `EncryptedSharedPreferences` (mobile equivalent to DPAPI on Windows).

3. **Static Sticker Converter (`domain/converter/StaticStickerConverter.kt`)**:
   - Resizes stickers to 512×512 while maintaining aspect ratio on a transparent canvas.
   - Automatically compresses WebP below WhatsApp's strict 100 KB limit using an iterative quality ladder.
   - Creates 96×96 PNG/WebP tray icons under 50 KB.

4. **Native WhatsApp Integration (`whatsapp/`)**:
   - `WhatsAppStickerProvider`: Directly registers as a WhatsApp Sticker ContentProvider (`com.stickerstudio.app.stickercontentprovider`).
   - `WhatsAppIntentHelper`: Triggers the native WhatsApp intent `com.whatsapp.intent.action.ENABLE_STICKER_PACK` so users can add packs with one tap without needing third-party helper apps.

5. **UI & Theme (`ui/`)**:
   - Built with Jetpack Compose & Material3 dark styling matching the HTML prototype.

## How to Open in Android Studio

1. Open Android Studio (Ladybug / Hedgehog or newer with JDK 17+).
2. Select **File > Open...** and choose this directory: `telegram-sticker-converter/android-app`.
3. Allow Gradle to sync dependencies.
4. Run on an emulator or physical device running Android 8.0+ (API 26+).
