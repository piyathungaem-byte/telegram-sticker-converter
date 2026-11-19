package com.stickerstudio.app.navigation

sealed class Screen(val route: String) {
    object Home : Screen("home")
    object Browse : Screen("browse")
    object Saved : Screen("saved")
    object Settings : Screen("settings")
    object Selection : Screen("selection/{packName}") {
        fun createRoute(packName: String) = "selection/$packName"
    }
    object Converting : Screen("converting/{packName}") {
        fun createRoute(packName: String) = "converting/$packName"
    }
    object Result : Screen("result/{packName}") {
        fun createRoute(packName: String) = "result/$packName"
    }
}
