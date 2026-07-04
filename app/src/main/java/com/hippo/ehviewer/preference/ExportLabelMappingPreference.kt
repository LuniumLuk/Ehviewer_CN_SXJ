package com.hippo.ehviewer.preference

import android.app.AlertDialog
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.AttributeSet
import android.widget.Toast
import androidx.preference.Preference
import com.hippo.ehviewer.EhApplication
import com.hippo.ehviewer.R
import com.hippo.ehviewer.Settings
import com.hippo.ehviewer.download.DownloadManager
import com.hippo.util.IoThreadPoolExecutor
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean

class ExportLabelMappingPreference : Preference {
    private val running = AtomicBoolean(false)
    private val mainHandler = Handler(Looper.getMainLooper())

    constructor(context: Context, attrs: AttributeSet?) : super(context, attrs)

    constructor(context: Context, attrs: AttributeSet?, defStyleAttr: Int) : super(
        context,
        attrs,
        defStyleAttr
    )

    override fun onClick() {
        super.onClick()

        if (!running.compareAndSet(false, true)) {
            return
        }

        val app = context.applicationContext as EhApplication
        val manager = EhApplication.getDownloadManager(app)
        val dir = Settings.getDownloadLocation()
        if (dir == null) {
            running.set(false)
            Toast.makeText(app, R.string.settings_download_invalid_download_location, Toast.LENGTH_SHORT)
                .show()
            return
        }

        val existingFile = dir.findFile(DownloadManager.DOWNLOAD_LABELS_MAPPING_FILE)
        if (existingFile != null && existingFile.isFile) {
            // File exists, show warning dialog
            AlertDialog.Builder(context)
                .setTitle(R.string.settings_download_export_label_mapping_exists_title)
                .setMessage(R.string.settings_download_export_label_mapping_exists_message)
                .setNegativeButton(R.string.settings_download_export_label_mapping_cancel) { _, _ ->
                    running.set(false)
                }
                .setNeutralButton(R.string.settings_download_export_label_mapping_backup) { _, _ ->
                    doBackupAndExport(app, manager, dir, existingFile)
                }
                .setPositiveButton(R.string.settings_download_export_label_mapping_override) { _, _ ->
                    doExport(app, manager, dir)
                }
                .setOnCancelListener { running.set(false) }
                .show()
        } else {
            doExport(app, manager, dir)
        }
    }

    private fun doBackupAndExport(
        app: EhApplication,
        manager: DownloadManager,
        dir: com.hippo.unifile.UniFile,
        existingFile: com.hippo.unifile.UniFile
    ) {
        val dateFormat = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US)
        val backupName = DownloadManager.DOWNLOAD_LABELS_MAPPING_FILE.replace(
            ".json", "-backup-${dateFormat.format(Date())}.json"
        )
        if (!existingFile.renameTo(backupName)) {
            running.set(false)
            Toast.makeText(app, R.string.settings_download_export_label_mapping_failed, Toast.LENGTH_SHORT)
                .show()
            return
        }
        doExport(app, manager, dir)
    }

    private fun doExport(app: EhApplication, manager: DownloadManager, dir: com.hippo.unifile.UniFile) {
        IoThreadPoolExecutor.instance.execute {
            val exported = manager.exportLabelsToFile(dir)
            mainHandler.post {
                val toastText = if (exported) {
                    app.getString(
                        R.string.settings_download_export_label_mapping_success,
                        DownloadManager.DOWNLOAD_LABELS_MAPPING_FILE
                    )
                } else {
                    app.getString(R.string.settings_download_export_label_mapping_failed)
                }
                Toast.makeText(app, toastText, Toast.LENGTH_SHORT).show()
                running.set(false)
            }
        }
    }
}