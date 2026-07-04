package com.hippo.ehviewer.download;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import com.hippo.ehviewer.dao.DownloadInfo;
import com.hippo.ehviewer.dao.DownloadLabelMapping;

import org.junit.Test;

import java.util.ArrayList;
import java.util.List;

public class DownloadManagerLabelExportTest {

  @Test
  public void testBuildLabelMappingWithNullAndSpecialLabels() {
    List<DownloadInfo> infos = new ArrayList<>();

    DownloadInfo first = new DownloadInfo(101L);
    first.setLabel("To Read");
    infos.add(first);

    DownloadInfo second = new DownloadInfo(102L);
    second.setLabel(null);
    infos.add(second);

    DownloadInfo third = new DownloadInfo(103L);
    third.setLabel("Label_!@#");
    infos.add(third);

    DownloadLabelMapping mapping = DownloadManager.buildLabelMapping(infos);

    assertEquals(DownloadManager.MAPPING_VERSION, mapping.version);
    assertEquals("To Read", mapping.labels.get(101L));
    assertNull(mapping.labels.get(102L));
    assertEquals("Label_!@#", mapping.labels.get(103L));
  }
}
