package cc.grepon.gatepath.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.os.IBinder
import android.util.Log
import dagger.hilt.android.AndroidEntryPoint

private const val TAG = "GatepathService"
private const val CHANNEL_ID = "gatepath_monitor"
private const val NOTIFICATION_ID = 1

/**
 * Foreground service that keeps the captive-portal monitor alive when the app
 * is backgrounded. The actual monitoring logic lives in [MainViewModel]; this
 * service exists solely to satisfy Android's foreground-service requirement for
 * long-running network work.
 */
@AndroidEntryPoint
class PortalMonitorService : Service() {

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        startForeground(NOTIFICATION_ID, buildNotification())
        Log.d(TAG, "PortalMonitorService started")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int =
        START_STICKY

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        super.onDestroy()
        Log.d(TAG, "PortalMonitorService destroyed")
    }

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Network Monitor",
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "Monitors for captive portal networks"
        }
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(channel)
    }

    private fun buildNotification(): Notification =
        Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("Gatepath")
            .setContentText("Monitoring for captive portals…")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setOngoing(true)
            .build()
}
