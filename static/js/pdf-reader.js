const PDFJS_VERSION = "6.2.108";
const PDFJS_BASE = `https://cdn.jsdelivr.net/npm/pdfjs-dist@${PDFJS_VERSION}`;
const PDFJS_MODULE = `${PDFJS_BASE}/build/pdf.min.mjs`;
const PDFJS_WORKER = `${PDFJS_BASE}/build/pdf.worker.min.mjs`;
const PAGE_WINDOW = 3;

const normalizeWord = (value) =>
    String(value || "")
        .normalize("NFKD")
        .replace(/\p{M}/gu, "")
        .toLocaleLowerCase("pt-BR")
        .replace(/[^\p{L}\p{N}]/gu, "");

const nextFrame = () =>
    new Promise((resolve) => window.requestAnimationFrame(resolve));

document.addEventListener("DOMContentLoaded", async () => {
    const reader = document.getElementById("pdfReader");
    if (!reader) return;

    const viewportElement = document.getElementById("pdfViewport");
    const documentElement = document.getElementById("pdfDocument");
    const statusElement = document.getElementById("pdfReaderStatus");
    const errorElement = document.getElementById("pdfReaderError");
    const followButton = document.getElementById("pdfFollowButton");
    const continueButton = document.getElementById("pdfContinueButton");
    const zoomInButton = document.getElementById("pdfZoomIn");
    const zoomOutButton = document.getElementById("pdfZoomOut");
    const zoomLabel = document.getElementById("pdfZoomLabel");
    const sourceElement = document.getElementById("readingText");
    const player = document.getElementById("readerPlayer");
    const canSeekAudio = Boolean(document.getElementById("playButton"));

    const sourceWords = [...document.querySelectorAll(".reading-word")].map(
        (element, index) => ({
            element,
            index,
            text: element.textContent,
            normalized: normalizeWord(element.textContent),
            charStart: Number(element.dataset.charStart),
            charEnd: Number(element.dataset.charEnd),
        })
    );

    let pdfjsLib = null;
    let pdfDocument = null;
    let pageRecords = [];
    let pageObserver = null;
    let pageRenderQueue = Promise.resolve();
    let renderGeneration = 0;
    let zoom = 1;
    let following = true;
    let activeCharStart = null;
    let pendingCharStart = null;
    let activeTargets = [];
    let selectedTargets = [];
    let targetsByCharStart = new Map();
    let pageByCharStart = new Map();

    const setStatus = (message) => {
        statusElement.textContent = message;
    };

    const setFollowing = (enabled, refocus = false) => {
        following = enabled;
        followButton.classList.toggle("active", enabled);
        followButton.setAttribute("aria-pressed", String(enabled));
        followButton.querySelector("span").textContent = enabled
            ? "Acompanhando"
            : "Retomar acompanhamento";
        if (enabled && refocus && activeCharStart !== null) {
            focusActiveWord(activeCharStart, true);
        }
    };

    const removeTargetReference = (charStart, target) => {
        const existing = targetsByCharStart.get(charStart) || [];
        const remaining = existing.filter((item) => item !== target);
        if (remaining.length) {
            targetsByCharStart.set(charStart, remaining);
        } else {
            targetsByCharStart.delete(charStart);
        }
    };

    const clearRecordTargets = (record) => {
        for (const {charStart, target} of record.targetEntries) {
            removeTargetReference(charStart, target);
        }
        record.targetEntries = [];
        record.targetLayer.replaceChildren();
    };

    const unloadRecord = (record) => {
        if (!record.rendered || record.renderPromise) return;
        clearRecordTargets(record);
        record.textLayer.replaceChildren();
        record.canvas.width = 1;
        record.canvas.height = 1;
        record.rendered = false;
    };

    const trimDistantPages = (centerPage) => {
        pageRecords.forEach((record) => {
            if (Math.abs(record.pageNumber - centerPage) > PAGE_WINDOW) {
                unloadRecord(record);
            }
        });
    };

    const showFallback = (message) => {
        reader.classList.remove("is-loading");
        setStatus("Leitor sincronizado indisponível");
        viewportElement.classList.add("d-none");
        errorElement.classList.remove("d-none");
        if (message) {
            errorElement.querySelector("span").textContent = message;
        }
        sourceElement.classList.remove("pdf-sync-source");
        sourceElement.removeAttribute("aria-hidden");
        sourceElement.querySelectorAll(".reading-word").forEach((word) => {
            word.tabIndex = 0;
            word.setAttribute("role", "button");
        });
        if (player) player.dataset.pdfMode = "false";
    };

    const shouldScrollTo = (target) => {
        const targetBounds = target.getBoundingClientRect();
        const viewportBounds = viewportElement.getBoundingClientRect();
        const safeTop = viewportBounds.top + viewportBounds.height * 0.22;
        const safeBottom = viewportBounds.bottom - viewportBounds.height * 0.22;
        return (
            targetBounds.top < safeTop ||
            targetBounds.bottom > safeBottom
        );
    };

    const scrollToTarget = (target, force = false) => {
        if (!following && !force) return;
        if (!force && !shouldScrollTo(target)) return;
        target.scrollIntoView({
            behavior: "smooth",
            block: "center",
            inline: "nearest",
        });
    };

    const focusActiveWord = (charStart, forceScroll = false) => {
        activeTargets.forEach((target) => target.classList.remove("active"));
        activeTargets = targetsByCharStart.get(Number(charStart)) || [];
        activeTargets.forEach((target) => target.classList.add("active"));
        if (activeTargets.length) {
            scrollToTarget(activeTargets[0], forceScroll);
            return;
        }

        const record = pageByCharStart.get(Number(charStart));
        if (!record) return;
        if ((following || forceScroll) && shouldScrollTo(record.pageElement)) {
            record.pageElement.scrollIntoView({
                behavior: "smooth",
                block: "center",
            });
        }
        if (!record.rendered && !record.renderPromise) {
            queuePageRender(record).then(() => {
                if (activeCharStart === Number(charStart)) {
                    focusActiveWord(charStart, forceScroll);
                }
            });
        }
    };

    const clearSelection = () => {
        selectedTargets.forEach((target) =>
            target.classList.remove("selected")
        );
        selectedTargets = [];
        pendingCharStart = null;
        continueButton.disabled = true;
        continueButton.classList.remove("active");
    };

    const selectWord = (charStart) => {
        clearSelection();
        pendingCharStart = Number(charStart);
        selectedTargets = targetsByCharStart.get(pendingCharStart) || [];
        selectedTargets.forEach((target) =>
            target.classList.add("selected")
        );
        continueButton.disabled = !canSeekAudio;
        continueButton.classList.toggle("active", canSeekAudio);
        setStatus(
            canSeekAudio
                ? "Ponto selecionado · confirme em “Continuar daqui”"
                : "Ponto selecionado · o áudio ainda está sendo preparado"
        );
    };

    const linkWords = (sourceIndexes, pdfIndexes, pdfWords) => {
        for (const sourceIndex of sourceIndexes) {
            const source = sourceWords[sourceIndex];
            if (!source) continue;
            for (const pdfIndex of pdfIndexes) {
                const pdfWord = pdfWords[pdfIndex];
                if (!pdfWord) continue;
                pdfWord.sourceStarts.add(source.charStart);
                if (!pageByCharStart.has(source.charStart)) {
                    pageByCharStart.set(source.charStart, pdfWord.record);
                }
            }
        }
    };

    const alignWords = (pdfWords) => {
        let sourceIndex = 0;
        let pdfIndex = 0;
        const linked = new Set();
        const lookAhead = 24;

        const link = (sourceIndexes, pdfIndexes) => {
            linkWords(sourceIndexes, pdfIndexes, pdfWords);
            sourceIndexes.forEach((index) => linked.add(index));
        };

        while (
            sourceIndex < sourceWords.length &&
            pdfIndex < pdfWords.length
        ) {
            const sourceWord = sourceWords[sourceIndex].normalized;
            const pdfWord = pdfWords[pdfIndex].normalized;

            if (!sourceWord) {
                sourceIndex += 1;
                continue;
            }
            if (!pdfWord) {
                pdfIndex += 1;
                continue;
            }
            if (sourceWord === pdfWord) {
                link([sourceIndex], [pdfIndex]);
                sourceIndex += 1;
                pdfIndex += 1;
                continue;
            }

            if (
                sourceWord ===
                pdfWord + (pdfWords[pdfIndex + 1]?.normalized || "")
            ) {
                link([sourceIndex], [pdfIndex, pdfIndex + 1]);
                sourceIndex += 1;
                pdfIndex += 2;
                continue;
            }
            if (
                pdfWord ===
                sourceWord +
                    (sourceWords[sourceIndex + 1]?.normalized || "")
            ) {
                link([sourceIndex, sourceIndex + 1], [pdfIndex]);
                sourceIndex += 2;
                pdfIndex += 1;
                continue;
            }

            let bestMatch = null;
            for (
                let sourceSkip = 0;
                sourceSkip <= lookAhead &&
                sourceIndex + sourceSkip < sourceWords.length;
                sourceSkip += 1
            ) {
                const candidateSource =
                    sourceWords[sourceIndex + sourceSkip].normalized;
                if (candidateSource.length < 3) continue;
                for (
                    let pdfSkip = 0;
                    pdfSkip <= lookAhead &&
                    pdfIndex + pdfSkip < pdfWords.length;
                    pdfSkip += 1
                ) {
                    if (
                        candidateSource !==
                        pdfWords[pdfIndex + pdfSkip].normalized
                    ) {
                        continue;
                    }
                    const score = sourceSkip + pdfSkip;
                    if (!bestMatch || score < bestMatch.score) {
                        bestMatch = {sourceSkip, pdfSkip, score};
                    }
                    break;
                }
            }
            if (bestMatch) {
                sourceIndex += bestMatch.sourceSkip;
                pdfIndex += bestMatch.pdfSkip;
                link([sourceIndex], [pdfIndex]);
                sourceIndex += 1;
                pdfIndex += 1;
                continue;
            }
            pdfIndex += 1;
        }

        return sourceWords.length
            ? Math.round((linked.size * 100) / sourceWords.length)
            : 0;
    };

    const wordsFromTextContent = (textContent, record) => {
        const words = [];
        for (const item of textContent.items) {
            if (typeof item.str !== "string") continue;
            for (const match of item.str.matchAll(/\S+/gu)) {
                words.push({
                    text: match[0],
                    normalized: normalizeWord(match[0]),
                    sourceStarts: new Set(),
                    record,
                });
            }
        }
        return words;
    };

    const renderedWordsFromLayer = (textLayer) => {
        const words = [];
        const walker = document.createTreeWalker(
            textLayer,
            NodeFilter.SHOW_TEXT
        );
        let node = walker.nextNode();
        while (node) {
            const parent = node.parentElement;
            if (
                parent?.closest(".textLayer") === textLayer &&
                !parent.classList.contains("endOfContent")
            ) {
                for (const match of node.data.matchAll(/\S+/gu)) {
                    words.push({
                        text: match[0],
                        node,
                        startOffset: match.index,
                        endOffset: match.index + match[0].length,
                    });
                }
            }
            node = walker.nextNode();
        }
        return words;
    };

    const wordRectangles = (word, pageBounds) => {
        const range = document.createRange();
        range.setStart(word.node, word.startOffset);
        range.setEnd(word.node, word.endOffset);
        return [...range.getClientRects()]
            .filter((rect) => rect.width > 1 && rect.height > 1)
            .map((rect) => ({
                left: rect.left - pageBounds.left,
                top: rect.top - pageBounds.top,
                width: rect.width,
                height: rect.height,
            }));
    };

    const buildRecordTargets = async (record) => {
        clearRecordTargets(record);
        await nextFrame();
        const renderedWords = renderedWordsFromLayer(record.textLayer);
        const pageBounds = record.pageElement.getBoundingClientRect();
        const length = Math.min(
            renderedWords.length,
            record.pageWords.length
        );

        for (let index = 0; index < length; index += 1) {
            const renderedWord = renderedWords[index];
            const logicalWord = record.pageWords[index];
            if (!logicalWord.sourceStarts.size) continue;

            for (const rectangle of wordRectangles(
                renderedWord,
                pageBounds
            )) {
                const target = document.createElement("span");
                target.className = "pdf-word-target";
                target.style.left = `${rectangle.left}px`;
                target.style.top = `${rectangle.top}px`;
                target.style.width = `${rectangle.width}px`;
                target.style.height = `${rectangle.height}px`;
                target.dataset.charStart = String(
                    Math.min(...logicalWord.sourceStarts)
                );
                target.title = renderedWord.text;
                record.targetLayer.appendChild(target);

                for (const charStart of logicalWord.sourceStarts) {
                    const existing =
                        targetsByCharStart.get(charStart) || [];
                    existing.push(target);
                    targetsByCharStart.set(charStart, existing);
                    record.targetEntries.push({charStart, target});
                }
            }
        }
    };

    const renderPage = async (record) => {
        if (record.generation !== renderGeneration) return;
        if (record.rendered) return;
        if (record.renderPromise) return record.renderPromise;
        const generation = record.generation;

        record.renderPromise = (async () => {
            const outputScale = Math.min(
                window.devicePixelRatio || 1,
                1.35
            );
            const context = record.canvas.getContext("2d", {
                alpha: false,
            });
            record.canvas.width = Math.floor(
                record.viewport.width * outputScale
            );
            record.canvas.height = Math.floor(
                record.viewport.height * outputScale
            );
            record.canvas.style.width = `${record.viewport.width}px`;
            record.canvas.style.height = `${record.viewport.height}px`;

            record.textLayer.replaceChildren();
            const textRenderer = new pdfjsLib.TextLayer({
                textContentSource: record.textContent,
                container: record.textLayer,
                viewport: record.viewport,
            });
            await Promise.all([
                record.page.render({
                    canvasContext: context,
                    viewport: record.viewport,
                    transform:
                        outputScale === 1
                            ? null
                            : [
                                  outputScale,
                                  0,
                                  0,
                                  outputScale,
                                  0,
                                  0,
                              ],
                }).promise,
                textRenderer.render(),
            ]);
            if (generation !== renderGeneration) return;
            await buildRecordTargets(record);
            record.rendered = true;
        })();

        try {
            await record.renderPromise;
        } finally {
            record.renderPromise = null;
        }
    };

    const queuePageRender = (record) => {
        if (record.rendered || record.renderPromise) {
            return record.renderPromise || Promise.resolve();
        }
        if (record.queued) return record.queuePromise;
        record.queued = true;
        record.queuePromise = pageRenderQueue = pageRenderQueue
            .catch(() => {})
            .then(() => renderPage(record))
            .finally(() => {
                record.queued = false;
                record.queuePromise = null;
            });
        return record.queuePromise;
    };

    const createPageRecord = async (
        pageNumber,
        pageStart,
        availableWidth
    ) => {
        const page = await pdfDocument.getPage(pageNumber);
        const originalViewport = page.getViewport({scale: 1});
        const fitScale = availableWidth / originalViewport.width;
        const pageViewport = page.getViewport({
            scale: fitScale * zoom,
        });

        const pageElement = document.createElement("section");
        pageElement.className = "pdf-page";
        pageElement.dataset.pageNumber = String(pageNumber);
        pageElement.style.width = `${pageViewport.width}px`;
        pageElement.style.height = `${pageViewport.height}px`;
        pageElement.style.setProperty(
            "--total-scale-factor",
            String(pageViewport.scale)
        );

        const canvas = document.createElement("canvas");
        canvas.className = "pdf-page-canvas";
        const textLayer = document.createElement("div");
        textLayer.className = "textLayer";
        textLayer.style.setProperty(
            "--total-scale-factor",
            String(pageViewport.scale)
        );
        const targetLayer = document.createElement("div");
        targetLayer.className = "pdf-word-layer";
        const pageLabel = document.createElement("span");
        pageLabel.className = "pdf-page-number";
        pageLabel.textContent = `Página ${pageNumber}`;
        pageElement.append(canvas, textLayer, targetLayer, pageLabel);
        documentElement.appendChild(pageElement);

        const textContent = await page.getTextContent({
            includeMarkedContent: true,
        });
        const record = {
            page,
            pageNumber,
            viewport: pageViewport,
            pageElement,
            canvas,
            textLayer,
            targetLayer,
            textContent,
            pageWords: [],
            targetEntries: [],
            rendered: false,
            renderPromise: null,
            queued: false,
            queuePromise: null,
            generation: renderGeneration,
        };
        record.pageWords = wordsFromTextContent(textContent, record);
        return record;
    };

    const renderDocument = async () => {
        const generation = ++renderGeneration;
        reader.classList.add("is-loading");
        pageObserver?.disconnect();
        documentElement.replaceChildren();
        targetsByCharStart = new Map();
        pageByCharStart = new Map();
        pageRecords = [];
        activeTargets = [];
        selectedTargets = [];
        setStatus("Preparando páginas…");

        const requestedStart = Math.max(
            1,
            Number(reader.dataset.pageStart || 1)
        );
        const requestedEnd = Number(reader.dataset.pageEnd || 0);
        const pageStart = Math.min(requestedStart, pdfDocument.numPages);
        const pageEnd = Math.min(
            requestedEnd > 0 ? requestedEnd : pdfDocument.numPages,
            pdfDocument.numPages
        );
        const availableWidth = Math.max(
            320,
            viewportElement.clientWidth - 32
        );
        const pdfWords = [];

        for (
            let pageNumber = pageStart;
            pageNumber <= pageEnd;
            pageNumber += 1
        ) {
            if (generation !== renderGeneration) return;
            setStatus(`Indexando página ${pageNumber} de ${pageEnd}…`);
            const record = await createPageRecord(
                pageNumber,
                pageStart,
                availableWidth
            );
            pageRecords.push(record);
            pdfWords.push(...record.pageWords);
        }

        if (generation !== renderGeneration) return;
        const coverage = alignWords(pdfWords);
        pageObserver = new IntersectionObserver(
            (entries) => {
                const visible = entries
                    .filter((entry) => entry.isIntersecting)
                    .sort(
                        (left, right) =>
                            right.intersectionRatio -
                            left.intersectionRatio
                    );
                visible.forEach((entry) => {
                    const record = pageRecords.find(
                        (item) => item.pageElement === entry.target
                    );
                    if (!record) return;
                    queuePageRender(record).then(() => {
                        if (activeCharStart !== null) {
                            focusActiveWord(activeCharStart);
                        }
                    });
                });
                const centerRecord = visible.length
                    ? pageRecords.find(
                          (item) =>
                              item.pageElement === visible[0].target
                      )
                    : null;
                if (centerRecord) {
                    trimDistantPages(centerRecord.pageNumber);
                }
            },
            {root: viewportElement, rootMargin: "700px 0px"}
        );
        pageRecords.forEach((record) =>
            pageObserver.observe(record.pageElement)
        );

        const rangeLabel =
            pageStart === pageEnd
                ? `Página ${pageStart}`
                : `Páginas ${pageStart}–${pageEnd}`;
        setStatus(
            coverage >= 85
                ? `${rangeLabel} · sincronização pronta`
                : `${rangeLabel} · sincronização parcial (${coverage}%)`
        );
        zoomLabel.textContent = `${Math.round(zoom * 100)}%`;
        reader.classList.remove("is-loading");
        if (activeCharStart !== null) {
            focusActiveWord(activeCharStart, true);
        } else {
            viewportElement.scrollTop = 0;
        }
    };

    const changeZoom = async (change) => {
        const nextZoom = Math.min(1.8, Math.max(0.65, zoom + change));
        if (nextZoom === zoom) return;
        zoom = nextZoom;
        zoomInButton.disabled = zoom >= 1.8;
        zoomOutButton.disabled = zoom <= 0.65;
        zoomLabel.textContent = `${Math.round(zoom * 100)}%`;
        clearSelection();
        await renderDocument();
    };

    followButton.addEventListener("click", () =>
        setFollowing(!following, true)
    );
    continueButton.addEventListener("click", () => {
        if (pendingCharStart === null || !canSeekAudio) return;
        const charStart = pendingCharStart;
        clearSelection();
        activeCharStart = charStart;
        setFollowing(true);
        focusActiveWord(charStart, true);
        document.dispatchEvent(
            new CustomEvent("projectlecture:seekword", {
                detail: {charStart},
            })
        );
    });
    zoomInButton.addEventListener("click", () => changeZoom(0.15));
    zoomOutButton.addEventListener("click", () => changeZoom(-0.15));
    ["wheel", "touchstart", "pointerdown"].forEach((eventName) => {
        viewportElement.addEventListener(
            eventName,
            () => setFollowing(false),
            {passive: true}
        );
    });
    viewportElement.addEventListener("keydown", (event) => {
        if (
            ["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End"].includes(
                event.key
            )
        ) {
            setFollowing(false);
        }
    });
    viewportElement.addEventListener("click", (event) => {
        const target = event.target.closest(".pdf-word-target");
        if (!target) return;
        selectWord(Number(target.dataset.charStart));
    });
    document.addEventListener("projectlecture:activeword", (event) => {
        activeCharStart = Number(event.detail?.charStart);
        focusActiveWord(activeCharStart);
    });
    document.addEventListener("projectlecture:playback", (event) => {
        if (event.detail?.playing) setFollowing(true, true);
    });

    try {
        if (!sourceWords.length) {
            throw new Error("O PDF não possui uma camada de texto sincronizável.");
        }
        pdfjsLib = await import(PDFJS_MODULE);
        pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_WORKER;
        pdfDocument = await pdfjsLib.getDocument({
            url: reader.dataset.pdfUrl,
            withCredentials: true,
        }).promise;
        await renderDocument();
    } catch (_) {
        showFallback(
            "Use o texto extraído abaixo ou abra o arquivo original. PDFs escaneados precisam de OCR."
        );
    }
});
