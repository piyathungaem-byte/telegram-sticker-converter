package com.stickerstudio.app

import android.app.Application

class StickerStudioApp : Application() {
    override fun onCreate() {
        super.onCreate()
        instance = this
    }

    companion object {
        lateinit var instance: StickerStudioApp
            private set
    }
}
