package com.hippo.ehviewer.dao;

import com.alibaba.fastjson.JSONException;
import com.alibaba.fastjson.JSONObject;

import java.util.HashMap;
import java.util.Map;

/**
 * DAO for serializing and deserializing download label mapping JSON.
 */
public class DownloadLabelMapping {

    public int version;
    public long exportTime;
    public final Map<Long, String> labels;

    public DownloadLabelMapping() {
        labels = new HashMap<>();
    }

    public JSONObject toJson() {
        JSONObject root = new JSONObject();
        root.put("version", version);
        root.put("exportTime", exportTime);

        JSONObject labelsObject = new JSONObject();
        if (labels != null) {
            for (Map.Entry<Long, String> entry : labels.entrySet()) {
                labelsObject.put(String.valueOf(entry.getKey()), entry.getValue());
            }
        }
        root.put("labels", labelsObject);
        return root;
    }

    public static DownloadLabelMapping fromJson(JSONObject json) throws JSONException {
        if (json == null) {
            throw new JSONException("Mapping JSON is null");
        }

        DownloadLabelMapping mapping = new DownloadLabelMapping();
        mapping.version = json.getIntValue("version");
        mapping.exportTime = json.getLongValue("exportTime");

        JSONObject labelsObject = json.getJSONObject("labels");
        if (labelsObject == null) {
            throw new JSONException("Missing labels object");
        }

        Map<Long, String> parsedLabels = new HashMap<>();
        for (String key : labelsObject.keySet()) {
            long gid;
            try {
                gid = Long.parseLong(key);
            } catch (NumberFormatException e) {
                throw new JSONException("Invalid gid key: " + key);
            }

            Object value = labelsObject.get(key);
            if (value == null) {
                parsedLabels.put(gid, null);
            } else if (value instanceof String) {
                parsedLabels.put(gid, (String) value);
            } else {
                throw new JSONException("Invalid label type for gid: " + key);
            }
        }

        mapping.labels.putAll(parsedLabels);
        return mapping;
    }

    public static DownloadLabelMapping fromJson(JSONObject json, int expectedVersion) throws JSONException {
        DownloadLabelMapping mapping = fromJson(json);
        if (mapping.version != expectedVersion) {
            throw new JSONException("mapping version mismatch: " + mapping.version);
        }
        return mapping;
    }
}
