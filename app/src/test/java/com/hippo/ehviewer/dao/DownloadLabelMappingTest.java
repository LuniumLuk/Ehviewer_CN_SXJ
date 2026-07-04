package com.hippo.ehviewer.dao;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import com.alibaba.fastjson.JSONException;
import com.alibaba.fastjson.JSONObject;

import org.junit.Test;

public class DownloadLabelMappingTest {

  @Test
  public void testRoundTripJson() {
    DownloadLabelMapping mapping = new DownloadLabelMapping();
    mapping.version = 1;
    mapping.exportTime = 123456789L;
    mapping.labels.put(1001L, "Favorites");
    mapping.labels.put(1002L, null);

    JSONObject json = mapping.toJson();
    DownloadLabelMapping parsed = DownloadLabelMapping.fromJson(json);

    assertEquals(1, parsed.version);
    assertEquals(123456789L, parsed.exportTime);
    assertEquals("Favorites", parsed.labels.get(1001L));
    assertNull(parsed.labels.get(1002L));
  }

  @Test(expected = JSONException.class)
  public void testInvalidGidKeyRejected() {
    JSONObject root = new JSONObject();
    root.put("version", 1);
    root.put("exportTime", 1L);

    JSONObject labels = new JSONObject();
    labels.put("bad-gid", "Label");
    root.put("labels", labels);

    DownloadLabelMapping.fromJson(root);
  }

  @Test(expected = JSONException.class)
  public void testMissingLabelsRejected() {
    JSONObject root = new JSONObject();
    root.put("version", 1);
    root.put("exportTime", 1L);

    DownloadLabelMapping.fromJson(root);
  }

  @Test(expected = JSONException.class)
  public void testVersionMismatchRejected() {
    JSONObject root = new JSONObject();
    root.put("version", 2);
    root.put("exportTime", 1L);
    root.put("labels", new JSONObject());

    DownloadLabelMapping.fromJson(root, 1);
  }
}
