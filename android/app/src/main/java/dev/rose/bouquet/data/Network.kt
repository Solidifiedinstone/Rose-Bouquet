package dev.rose.bouquet.data

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities

/**
 * What kind of connection this phone is on.
 *
 * Needed for exactly two decisions, both about spending somebody's data
 * allowance without being asked: whether to stream, and whether to download.
 */
object Network {

    /** True on wifi or ethernet — anything the user is not billed per megabyte for. */
    fun unmetered(context: Context): Boolean {
        val manager = context.getSystemService(ConnectivityManager::class.java) ?: return true
        val capabilities = manager.getNetworkCapabilities(manager.activeNetwork) ?: return false
        // Trusting NET_CAPABILITY_NOT_METERED rather than checking for a wifi
        // transport: a metered hotspot is wifi, and a phone tethered to it is
        // still paying by the megabyte.
        return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)
    }

    fun online(context: Context): Boolean {
        val manager = context.getSystemService(ConnectivityManager::class.java) ?: return false
        val capabilities = manager.getNetworkCapabilities(manager.activeNetwork) ?: return false
        return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }
}
