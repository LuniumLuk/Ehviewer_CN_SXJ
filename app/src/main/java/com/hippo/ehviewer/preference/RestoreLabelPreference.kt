/*
 * Copyright 2016 Hippo Seven
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.hippo.ehviewer.preference

import android.app.Activity
import com.hippo.app.ProgressDialog
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.AttributeSet
import android.util.Log
import android.widget.Toast
import androidx.preference.Preference
import com.alibaba.fastjson.JSON
import com.alibaba.fastjson.JSONException
import com.hippo.ehviewer.EhApplication
import com.hippo.ehviewer.EhDB
import com.hippo.ehviewer.R
import com.hippo.ehviewer.Settings
import com.hippo.ehviewer.dao.DownloadLabelMapping
import com.hippo.ehviewer.download.DownloadManager
import com.hippo.util.ExceptionUtils.throwIfFatal
import com.hippo.util.IoThreadPoolExecutor
import java.util.concurrent.atomic.AtomicBoolean

class RestoreLabelPreference : Preference {
    private var mTask: RestoreTask? = null

    constructor(context: Context, attrs: AttributeSet?) : super(context, attrs)

    constructor(context: Context, attrs: AttributeSet?, defStyleAttr: Int) : super(
        context,
        attrs,
        defStyleAttr
    )

    override fun onClick() {
        super.onClick()
        if (mTask == null) {
            mTask = RestoreTask(context).also { it.start() }
        }
    }

    override fun onDetached() {
        mTask?.cancel()
        mTask = null
        super.onDetached()
    }

    private inner class RestoreTask(private val mContext: Context) {
        private val mApplication: EhApplication =
            mContext.applicationContext as EhApplication
        private val mManager: DownloadManager =
            EhApplication.getDownloadManager(mApplication)
        private val mHandler = Handler(Looper.getMainLooper())
        private val mCancelled = AtomicBoolean(false)
        private var mProgressDialog: ProgressDialog? = null

        fun start() {
            onPreExecute()
            IoThreadPoolExecutor.instance.execute {
                val result = doInBackground()
                mHandler.post {
                    if (mCancelled.get()) {
                        onCancelled()
                    } else {
                        onPostExecute(result)
                    }
                }
            }
        }

        fun cancel() {
            mCancelled.set(true)
        }

        private fun onPreExecute() {
            mProgressDialog = ProgressDialog(mContext)
            mProgressDialog!!.setTitle(R.string.settings_download_restore_labels)
            mProgressDialog!!.setMessage(mApplication.getString(R.string.settings_download_restore_labels_progress))
            mProgressDialog!!.setIndeterminate(true)
            mProgressDialog!!.setProgressStyle(ProgressDialog.STYLE_SPINNER)
            mProgressDialog!!.setCancelable(true)
            mProgressDialog!!.setOnCancelListener { cancel() }
            mProgressDialog!!.show()
        }

        private fun loadMappingFile(): DownloadLabelMapping {
            val dir = Settings.getDownloadLocation()
                ?: throw IllegalStateException(mApplication.getString(R.string.settings_download_restore_labels_not_found))
            val mappingFile = dir.findFile(DownloadManager.DOWNLOAD_LABELS_MAPPING_FILE)
                ?: throw IllegalStateException(mApplication.getString(R.string.settings_download_restore_labels_not_found))
            if (!mappingFile.isFile()) {
                throw IllegalStateException(mApplication.getString(R.string.settings_download_restore_labels_not_found))
            }

            if (mappingFile.length() > MAX_MAPPING_FILE_SIZE) {
                throw JSONException("mapping file too large")
            }

            val mappingJsonText = mappingFile.openInputStream().use { inputStream ->
                inputStream.readBytes().toString(Charsets.UTF_8)
            }

            val jsonObject = JSON.parseObject(mappingJsonText)
                ?: throw JSONException("mapping root is empty")
            return DownloadLabelMapping.fromJson(jsonObject, DownloadManager.MAPPING_VERSION)
        }

        private fun doInBackground(): RestoreResult {
            if (mCancelled.get()) {
                return RestoreResult()
            }

            return try {
                val mapping = loadMappingFile()
                validateMapping(mapping)
                val updates = linkedMapOf<Long, String>()
                val labelsToEnsure = linkedSetOf<String>()
                val createdLabels = linkedSetOf<String>()
                var skipped = 0

                for ((gid, rawLabel) in mapping.labels) {
                    if (mCancelled.get()) {
                        return RestoreResult()
                    }

                    val label = rawLabel?.trim()
                    if (label.isNullOrEmpty()) {
                        skipped++
                        continue
                    }

                    val info = EhDB.getDownloadInfo(gid)
                    if (info == null) {
                        skipped++
                        continue
                    }

                    if (info.label == label) {
                        continue
                    }

                    updates[gid] = label
                    labelsToEnsure.add(label)
                    if (!mManager.containLabel(label)) {
                        createdLabels.add(label)
                    }
                }

                EhDB.applyDownloadLabelMapping(updates, labelsToEnsure)
                val inMemoryApplied = mManager.applyLabelUpdatesInSyncThread(updates, createdLabels)
                if (inMemoryApplied < updates.size) {
                    Log.w(TAG, "In-memory sync: $inMemoryApplied/${updates.size} entries updated, DB updated all ${updates.size}")
                }
                val removed = mManager.removeEmptyLabelsInSyncThread()
                if (removed > 0) {
                    Log.i(TAG, "Removed $removed empty labels after restore")
                }

                RestoreResult(applied = updates.size, skipped = skipped)
            } catch (e: IllegalStateException) {
                RestoreResult(errorMessage = e.message ?: mApplication.getString(R.string.settings_download_restore_labels_not_found))
            } catch (e: JSONException) {
                Log.w(TAG, "Invalid label mapping file", e)
                val detail = e.message ?: "unknown"
                RestoreResult(errorMessage = mApplication.getString(R.string.settings_download_restore_labels_invalid, detail))
            } catch (e: Throwable) {
                throwIfFatal(e)
                Log.e(TAG, "Failed to restore labels", e)
                RestoreResult(errorMessage = mApplication.getString(R.string.settings_download_restore_labels_failed))
            }
        }

        private fun validateMapping(mapping: DownloadLabelMapping) {
            var nonEmptyLabels = 0
            var gidMirroredLabels = 0

            for ((gid, rawLabel) in mapping.labels) {
                val label = rawLabel?.trim()
                if (label.isNullOrEmpty()) {
                    continue
                }
                nonEmptyLabels++
                if (label == gid.toString()) {
                    gidMirroredLabels++
                }
            }

            // Reject known-bad mappings generated by old fallback logic (gid serialized as label text).
            if (nonEmptyLabels >= 20 && gidMirroredLabels * 100 >= nonEmptyLabels * 90) {
                throw JSONException("mapping appears corrupted: labels mirror gids")
            }
        }

        val isContextValid: Boolean
            get() {
                if (mContext is Activity) {
                    val activity = mContext
                    return !activity.isFinishing && !activity.isDestroyed
                }
                return true
            }

        fun dismissProgressDialog() {
            if (mProgressDialog == null) {
                return
            }
            if (this.isContextValid) {
                try {
                    if (mProgressDialog!!.isShowing) {
                        mProgressDialog!!.dismiss()
                    }
                } catch (e: IllegalArgumentException) {
                    throwIfFatal(e)
                }
            }
            mProgressDialog = null
        }

        private fun onCancelled() {
            mTask = null
            dismissProgressDialog()
        }

        private fun onPostExecute(result: RestoreResult) {
            mTask = null
            dismissProgressDialog()

            if (!result.errorMessage.isNullOrEmpty()) {
                Toast.makeText(mApplication, result.errorMessage, Toast.LENGTH_SHORT).show()
                return
            }

            val message = if (result.applied == 0 && result.skipped == 0) {
                mApplication.getString(R.string.settings_download_restore_labels_no_change)
            } else {
                mApplication.getString(
                    R.string.settings_download_restore_labels_success,
                    result.applied,
                    result.skipped
                )
            }
            Toast.makeText(mApplication, message, Toast.LENGTH_SHORT).show()

            if (this.isContextValid && mContext is Activity) {
                mContext.setResult(Activity.RESULT_OK)
            }
        }
    }

    private data class RestoreResult(
        val applied: Int = 0,
        val skipped: Int = 0,
        val errorMessage: String? = null,
    )

    companion object {
        private val TAG: String = RestoreLabelPreference::class.java.simpleName
        private const val MAX_MAPPING_FILE_SIZE = 10L * 1024L * 1024L
    }
}
