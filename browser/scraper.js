(async function scrapeDiscord() {
  // --- CONFIG: edit before running for channel-specific noise ---
  const SKIP_PATTERNS = [
    /^Posted to:/i,
    /^Failed:/i,
  ];

  const allMessages = new Map();
  const imageURLs = [];           // {url, timestamp}
  const videoURLs = [];           // {url, timestamp}
  const fileURLs = [];            // {url, timestamp}
  const scroller = document.querySelector('[class*="managedReactiveScroller"]')
                || document.querySelector('[data-jump-section="global"][role="group"]')
                || document.querySelector('[class*="scroller__36d07"]');

  if (!scroller) {
    console.error('Could not find message scroller. Make sure you are in a Discord channel.');
    return;
  }
  console.log('%c[Discord Scraper] Found scroller:', 'color: cyan', scroller);

  function isJunkImage(src) {
    return /cdn\.discordapp\.com\/emojis\//i.test(src)
        || /cdn\.discordapp\.com\/stickers\//i.test(src)
        || /cdn\.discordapp\.com\/avatars\//i.test(src)
        || /cdn\.discordapp\.com\/clan-badges\//i.test(src)
        || /twemoji/i.test(src)
        || /emoji/i.test(src);
  }

  function extractEmbeds(el) {
    const wrappers = el.querySelectorAll('[class*="embedWrapper"], [class*="embedFull"]');
    const embeds = [];
    wrappers.forEach(w => {
      const authorEl = w.querySelector('[class*="embedAuthorName"], [class*="embedAuthor"]');
      const titleEl = w.querySelector('[class*="embedTitle"]');
      const titleLink = titleEl && (titleEl.closest('a') || titleEl.querySelector('a'));
      const descEl = w.querySelector('[class*="embedDescription"]');
      const footerEl = w.querySelector('[class*="embedFooterText"], [class*="embedFooter"]');
      const fieldEls = w.querySelectorAll('[class*="embedField"]');
      const fields = [];
      fieldEls.forEach(f => {
        const n = f.querySelector('[class*="embedFieldName"]');
        const v = f.querySelector('[class*="embedFieldValue"]');
        if (n && v) fields.push({ name: n.innerText.trim(), value: v.innerText.trim() });
      });
      const embed = {
        author: authorEl ? authorEl.innerText.trim() : '',
        title:  titleEl  ? titleEl.innerText.trim()  : '',
        url:    titleLink ? titleLink.href : '',
        description: descEl ? descEl.innerText.trim() : '',
        fields,
        footer: footerEl ? footerEl.innerText.trim() : '',
      };
      if (embed.author || embed.title || embed.url || embed.description || embed.fields.length || embed.footer) {
        embeds.push(embed);
      }
    });
    return embeds;
  }

  function extractVisible() {
    const msgElements = document.querySelectorAll('[id^="chat-messages-"]');
    msgElements.forEach(el => {
      const id = el.id;
      if (allMessages.has(id)) return;

      const timeEl = el.querySelector('time');
      const timestamp = timeEl ? timeEl.getAttribute('datetime') : null;

      const authorEl = el.querySelector('[class*="username_"]')
                    || el.querySelector('[class*="headerText_"] span');
      const author = authorEl ? authorEl.textContent.trim() : '__continued__';

      const contentEl = el.querySelector('[id^="message-content-"]');
      const content = contentEl ? contentEl.innerText.trim() : '';

      // Noise filter — drop cross-posting bot / changelog relay rows
      if (content && SKIP_PATTERNS.some(p => p.test(content))) return;

      const linkEls = contentEl ? contentEl.querySelectorAll('a[href]') : [];
      const links = Array.from(linkEls).map(a => a.href);

      const embedLinkEls = el.querySelectorAll('[class*="embed_"] a[href]');
      const embedLinks = Array.from(embedLinkEls)
        .map(a => a.href)
        .filter(href => !links.includes(href));

      const embedTextEls = el.querySelectorAll('[class*="embedTitle_"], [class*="embedDescription_"]');
      const embedText = Array.from(embedTextEls).map(e => e.innerText.trim()).filter(Boolean);

      const embeds = extractEmbeds(el);

      const imgEls = el.querySelectorAll(
        '[class*="imageWrapper_"] img, ' +
        '[class*="embedImage_"] img, ' +
        '[class*="attachment_"] img, ' +
        'img[src*="attachments/"], ' +
        'img[src*="cdn.discordapp.com"]'
      );
      const images = [];
      const seen = new Set();
      imgEls.forEach(img => {
        let src = img.closest('a') ? img.closest('a').href : (img.src || '');
        try {
          const u = new URL(src);
          u.searchParams.delete('width');
          u.searchParams.delete('height');
          u.searchParams.delete('size');
          src = u.toString();
        } catch (_) {}
        if (src && !seen.has(src) && !src.startsWith('data:') && !isJunkImage(src)) {
          seen.add(src);
          images.push(src);
          imageURLs.push({ url: src, timestamp: timestamp || '' });
        }
      });

      const videoEls = el.querySelectorAll('video source, [class*="attachment_"] a[href*=".mp4"], a[href*=".webm"]');
      const videos = Array.from(new Set(
        Array.from(videoEls).map(v => v.src || v.href).filter(Boolean)
      ));
      videos.forEach(v => {
        if (!videoURLs.some(x => x.url === v)) videoURLs.push({ url: v, timestamp: timestamp || '' });
      });

      const fileEls = el.querySelectorAll('[class*="attachment_"] a[href*="cdn.discordapp.com"]');
      const files = Array.from(fileEls)
        .map(a => a.href)
        .filter(href => {
          const lower = href.toLowerCase();
          return !lower.match(/\.(png|jpg|jpeg|gif|webp|mp4|webm|mov)(\?|$)/);
        });
      files.forEach(f => {
        if (!fileURLs.some(x => x.url === f)) fileURLs.push({ url: f, timestamp: timestamp || '' });
      });

      allMessages.set(id, {
        id,
        timestamp: timestamp || '',
        author,
        content,
        links,
        embedLinks,
        embedText,
        embeds,
        images,
        videos,
        files,
      });
    });
  }

  function datePrefix(timestamp) {
    return timestamp ? timestamp.slice(0, 10) : 'undated';
  }
  function originalFilename(url) {
    try {
      const name = decodeURIComponent(new URL(url).pathname.split('/').pop());
      return name.replace(/[ :]/g, '_');
    } catch (_) { return null; }
  }
  function buildFilename(url, timestamp, fallbackIdx, fallbackExt) {
    const orig = originalFilename(url);
    const prefix = datePrefix(timestamp);
    if (orig) return `${prefix}_${orig}`;
    return `${prefix}_discord-${String(fallbackIdx).padStart(4, '0')}.${fallbackExt}`;
  }

  // --- SCROLL UP AND COLLECT ---
  let staleCount = 0;
  const maxStale = 15;
  console.log('%c[Discord Scraper] Starting — scrolling to top...', 'color: cyan; font-weight: bold');

  while (staleCount < maxStale) {
    const prevSize = allMessages.size;
    extractVisible();
    scroller.scrollTo({ top: 0, behavior: 'instant' });
    await new Promise(r => setTimeout(r, 2500));
    extractVisible();

    if (allMessages.size === prevSize) {
      staleCount++;
      console.log(`[Discord Scraper] No new messages (${staleCount}/${maxStale})... ${allMessages.size} total`);
    } else {
      staleCount = 0;
      console.log(`[Discord Scraper] ${allMessages.size} messages collected`);
    }
  }

  extractVisible();
  console.log('%c[Discord Scraper] Scrolling complete.', 'color: lime; font-weight: bold');

  // --- BACKFILL AUTHORS FOR GROUPED MESSAGES ---
  const sorted = Array.from(allMessages.values())
    .sort((a, b) => {
      if (a.timestamp && b.timestamp) return new Date(a.timestamp) - new Date(b.timestamp);
      return a.id.localeCompare(b.id);
    });

  let lastAuthor = '';
  let lastTimestamp = '';
  for (const msg of sorted) {
    if (msg.author === '__continued__') {
      msg.author = lastAuthor;
    } else {
      lastAuthor = msg.author;
    }
    if (!msg.timestamp && lastTimestamp) {
      msg.timestamp = lastTimestamp;
    } else if (msg.timestamp) {
      lastTimestamp = msg.timestamp;
    }
  }

  sorted.forEach(m => delete m.id);

  const stats = {
    totalMessages: sorted.length,
    uniqueAuthors: [...new Set(sorted.map(m => m.author))],
    totalImages: imageURLs.length,
    totalVideos: videoURLs.length,
    totalFiles: fileURLs.length,
    dateRange: sorted.length
      ? `${sorted[0].timestamp} → ${sorted[sorted.length - 1].timestamp}`
      : 'N/A',
  };
  console.log('%c[Discord Scraper] Stats:', 'color: yellow; font-weight: bold', stats);

  // --- DOWNLOAD JSON ---
  const output = { stats, messages: sorted };
  const dateStamp = new Date().toISOString().slice(0, 10);

  function downloadText(text, filename, mime) {
    const blob = new Blob([text], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  downloadText(JSON.stringify(output, null, 2), `discord-export-${dateStamp}.json`, 'application/json');
  console.log('%c[Discord Scraper] JSON downloaded!', 'color: lime; font-weight: bold');

  // --- BUILD + DOWNLOAD MARKDOWN CHANGELOG ---
  function renderEmbed(emb) {
    const parts = [];
    if (emb.author) parts.push(`**${emb.author}**`);
    if (emb.title)  parts.push(emb.url ? `[${emb.title}](${emb.url})` : `**${emb.title}**`);
    else if (emb.url) parts.push(`[Embedded Link](${emb.url})`);
    if (emb.description) parts.push(emb.description);
    for (const f of emb.fields) parts.push(`**${f.name}:** ${f.value}`);
    if (emb.footer) parts.push(`_${emb.footer}_`);
    return parts.join('\n');
  }

  const mdLines = [`# Discord Export\n\n_Exported ${dateStamp} — ${stats.totalMessages} messages, ${stats.dateRange}_\n`];
  for (const msg of sorted) {
    const date = msg.timestamp ? msg.timestamp.slice(0, 10) : '(no date)';
    mdLines.push(`### ${date} - ${msg.author}\n`);
    if (msg.content) mdLines.push(`${msg.content}\n`);
    for (const img of msg.images) {
      const fname = originalFilename(img);
      const localPath = fname ? `attachments/${datePrefix(msg.timestamp)}_${fname}` : img;
      mdLines.push(`![](${localPath})\n`);
    }
    for (const emb of (msg.embeds || [])) {
      const r = renderEmbed(emb);
      if (r) mdLines.push(`${r}\n`);
    }
    if ((!msg.embeds || msg.embeds.length === 0) && msg.embedText.length) {
      for (const t of msg.embedText) mdLines.push(`${t}\n`);
    }
  }
  downloadText(mdLines.join('\n---\n'), `discord-export-${dateStamp}.md`, 'text/markdown');
  console.log('%c[Discord Scraper] Markdown changelog downloaded!', 'color: lime; font-weight: bold');

  // --- DOWNLOAD ALL ATTACHMENTS (full mirror) ---
  const totalAttachments = imageURLs.length + videoURLs.length + fileURLs.length;
  if (totalAttachments > 0) {
    const doDownload = confirm(
      `Full mirror: ${imageURLs.length} images, ${videoURLs.length} videos, ${fileURLs.length} files.\n\n` +
      `Tiny images < 10KB (emoji) will be auto-skipped.\n` +
      `Download all ${totalAttachments} attachments?`
    );
    if (doDownload) {
      const MIN_IMG_SIZE = 10 * 1024;

      async function downloadBlob(blob, filename) {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(a.href);
      }

      if (imageURLs.length > 0) {
        let skipped = 0, downloaded = 0;
        console.log(`%c[Discord Scraper] Downloading ${imageURLs.length} images...`, 'color: cyan');
        for (let i = 0; i < imageURLs.length; i++) {
          const { url: imgUrl, timestamp } = imageURLs[i];
          try {
            const resp = await fetch(imgUrl);
            const blob = await resp.blob();
            if (blob.size < MIN_IMG_SIZE) { skipped++; continue; }
            const ext = imgUrl.match(/\.(png|jpg|jpeg|gif|webp)/i)?.[1] || 'png';
            const filename = buildFilename(imgUrl, timestamp, downloaded + 1, ext);
            await downloadBlob(blob, filename);
            downloaded++;
            if (downloaded % 5 === 0) await new Promise(r => setTimeout(r, 500));
          } catch (err) { console.warn(`[Discord Scraper] Failed image ${i + 1}:`, err); }
        }
        console.log(`%c[Discord Scraper] Images: ${downloaded} downloaded, ${skipped} skipped.`, 'color: lime');
      }

      if (videoURLs.length > 0) {
        console.log(`%c[Discord Scraper] Downloading ${videoURLs.length} videos...`, 'color: cyan');
        for (let i = 0; i < videoURLs.length; i++) {
          const { url: vidUrl, timestamp } = videoURLs[i];
          try {
            const resp = await fetch(vidUrl);
            const blob = await resp.blob();
            const ext = vidUrl.match(/\.(mp4|webm|mov)/i)?.[1] || 'mp4';
            const filename = buildFilename(vidUrl, timestamp, i + 1, ext);
            await downloadBlob(blob, filename);
            console.log(`[Discord Scraper] Video ${i + 1}/${videoURLs.length}: ${filename} (${(blob.size / 1024 / 1024).toFixed(1)}MB)`);
            await new Promise(r => setTimeout(r, 1000));
          } catch (err) { console.warn(`[Discord Scraper] Failed video ${i + 1}:`, err); }
        }
        console.log(`%c[Discord Scraper] Videos: ${videoURLs.length} downloaded.`, 'color: lime');
      }

      if (fileURLs.length > 0) {
        console.log(`%c[Discord Scraper] Downloading ${fileURLs.length} files...`, 'color: cyan');
        for (let i = 0; i < fileURLs.length; i++) {
          const { url: fileUrl, timestamp } = fileURLs[i];
          try {
            const resp = await fetch(fileUrl);
            const blob = await resp.blob();
            const filename = buildFilename(fileUrl, timestamp, i + 1, 'bin');
            await downloadBlob(blob, filename);
            console.log(`[Discord Scraper] File ${i + 1}/${fileURLs.length}: ${filename} (${(blob.size / 1024).toFixed(0)}KB)`);
            if (i % 3 === 2) await new Promise(r => setTimeout(r, 500));
          } catch (err) { console.warn(`[Discord Scraper] Failed file ${i + 1}:`, err); }
        }
        console.log(`%c[Discord Scraper] Files: ${fileURLs.length} downloaded.`, 'color: lime');
      }
    }
  }

  console.log('%c[Discord Scraper] All done!', 'color: lime; font-size: 16px; font-weight: bold');
  return output;
})();
