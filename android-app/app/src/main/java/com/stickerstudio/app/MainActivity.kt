package com.stickerstudio.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.stickerstudio.app.navigation.AppNavigation
import com.stickerstudio.app.ui.theme.StickerStudioTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            StickerStudioTheme {
                AppNavigation()
            }
        }
    }
}
