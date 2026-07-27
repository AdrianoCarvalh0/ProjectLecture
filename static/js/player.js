document.addEventListener("DOMContentLoaded", () => {
    const root = document.getElementById("readerPlayer");
    const audio = document.getElementById("audioPlayer");
    if (!root || !audio) return;

    const playButton = document.getElementById("playButton");
    const seekRange = document.getElementById("seekRange");
    const currentTime = document.getElementById("currentTime");
    const totalTime = document.getElementById("totalTime");
    const playbackRate = document.getElementById("playbackRate");
    const streamStatus = document.getElementById("streamStatus");
    const readingPosition = document.getElementById("readingPosition");
    const readingText = document.getElementById("readingText");
    const words = [...document.querySelectorAll(".reading-word")];
    const wordsByStart = new Map(
        words.map((word) => [Number(word.dataset.charStart), word])
    );

    let manifest = null;
    let chunks = [];
    let chunkIndex = -1;
    let activeWord = null;
    let activeTiming = null;
    let desiredLoad = null;
    let loadVersion = 0;
    let pollTimer = null;
    let lastSavedAt = 0;
    let initialized = false;
    let switchingSource = false;

    const formatTime = (seconds) => {
        const safe = Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
        const hours = Math.floor(safe / 3600);
        const minutes = Math.floor((safe % 3600) / 60);
        const secs = Math.floor(safe % 60);
        return hours
            ? `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`
            : `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
    };

    const currentChunk = () => chunks[chunkIndex] || null;
    const readyChunk = (chunk) => chunk?.status === "ready" && Boolean(chunk.audio_url);
    const totalDuration = () => {
        const declared = Number(manifest?.duration_seconds || 0);
        const generated = chunks.reduce(
            (maximum, chunk) => Math.max(maximum, Number(chunk.end_seconds || 0)),
            0
        );
        return Math.max(declared, generated);
    };

    const setStatus = (message, waiting = false) => {
        if (!streamStatus) return;
        streamStatus.textContent = message;
        streamStatus.classList.toggle("is-waiting", waiting);
    };

    const updateDuration = () => {
        const duration = totalDuration();
        seekRange.max = Math.max(duration, 0.1);
        totalTime.textContent = manifest?.building
            ? `${formatTime(duration)} gerados`
            : formatTime(duration);
    };

    const findChunkForChar = (charOffset) => {
        if (!chunks.length) return -1;
        const exact = chunks.findIndex(
            (chunk) =>
                charOffset >= Number(chunk.start_char) &&
                charOffset < Number(chunk.end_char)
        );
        if (exact >= 0) return exact;
        if (charOffset >= Number(chunks.at(-1).end_char)) return chunks.length - 1;
        return 0;
    };

    const findChunkForTime = (seconds) => {
        const exact = chunks.findIndex(
            (chunk) =>
                seconds >= Number(chunk.start_seconds) &&
                seconds < Number(chunk.end_seconds)
        );
        if (exact >= 0) return exact;
        const lastReady = chunks.findLastIndex((chunk) => readyChunk(chunk));
        if (lastReady >= 0 && seconds <= Number(chunks[lastReady].end_seconds)) {
            return lastReady;
        }
        return Math.min(lastReady + 1, chunks.length - 1);
    };

    const timingForChar = (chunk, charOffset) => {
        const timings = chunk?.word_timings || [];
        return (
            timings.find(
                (timing) =>
                    charOffset >= Number(timing.char_start) &&
                    charOffset < Number(timing.char_end)
            ) ||
            timings.find((timing) => Number(timing.char_start) >= charOffset) ||
            timings.at(-1) ||
            null
        );
    };

    const timingAt = (chunk, seconds) => {
        const timings = chunk?.word_timings || [];
        return (
            timings.find(
                (timing) =>
                    seconds >= Number(timing.start) &&
                    seconds < Number(timing.end)
            ) ||
            (seconds >= Number(timings.at(-1)?.end) ? timings.at(-1) : null)
        );
    };

    const showActiveTiming = (timing) => {
        const nextWord = timing
            ? wordsByStart.get(Number(timing.char_start)) || null
            : null;
        if (nextWord === activeWord) return;

        activeWord?.classList.remove("active");
        activeWord = nextWord;
        activeTiming = timing;
        activeWord?.classList.add("active");

        if (!activeWord) return;
        const wordNumber = words.indexOf(activeWord) + 1;
        if (readingPosition) {
            readingPosition.textContent = `Palavra ${wordNumber} de ${words.length}`;
        }
        const wordBounds = activeWord.getBoundingClientRect();
        const textBounds = readingText.getBoundingClientRect();
        if (
            wordBounds.top < textBounds.top + 32 ||
            wordBounds.bottom > textBounds.bottom - 32
        ) {
            activeWord.scrollIntoView({behavior: "smooth", block: "center"});
        }
    };

    const waitForMetadata = () => {
        if (audio.readyState >= HTMLMediaElement.HAVE_METADATA) {
            return Promise.resolve();
        }
        return new Promise((resolve, reject) => {
            audio.addEventListener("loadedmetadata", resolve, {once: true});
            audio.addEventListener("error", reject, {once: true});
        });
    };

    const prefetchNext = () => {
        const next = chunks[chunkIndex + 1];
        if (!readyChunk(next)) return;
        const preload = new Audio();
        preload.preload = "auto";
        preload.src = next.audio_url;
    };

    const loadChunk = async (
        index,
        {autoplay = false, charOffset = null, localTime = null} = {}
    ) => {
        const chunk = chunks[index];
        if (!readyChunk(chunk)) {
            desiredLoad = {order: chunk?.order, autoplay, charOffset, localTime};
            setStatus("Preparando este trecho da leitura…", true);
            scheduleRefresh(900);
            return;
        }

        desiredLoad = null;
        const version = ++loadVersion;
        const sourceChanged = audio.src !== new URL(chunk.audio_url, window.location.href).href;
        chunkIndex = index;

        if (sourceChanged) {
            switchingSource = true;
            audio.src = chunk.audio_url;
            audio.load();
        }

        try {
            await waitForMetadata();
        } catch (_) {
            if (version === loadVersion) {
                setStatus("Não foi possível carregar este trecho.", true);
            }
            return;
        }
        if (version !== loadVersion) return;
        switchingSource = false;

        let target = localTime;
        if (charOffset !== null) {
            target = Number(timingForChar(chunk, charOffset)?.start || 0);
        }
        if (target !== null) {
            audio.currentTime = Math.min(
                Math.max(0, Number(target)),
                Math.max(0, audio.duration - 0.05)
            );
        }
        audio.playbackRate = Number(playbackRate.value);
        setStatus(
            manifest?.building
                ? `Trecho ${index + 1} pronto · os próximos continuam sendo preparados`
                : `Trecho ${index + 1} de ${chunks.length}`
        );
        prefetchNext();
        updatePosition();

        if (autoplay) {
            try {
                await audio.play();
            } catch (_) {
                setStatus("Toque em reproduzir para continuar.");
            }
        }
    };

    const refreshManifest = async () => {
        try {
            const response = await fetch(root.dataset.manifestUrl, {
                headers: {"Accept": "application/json"},
            });
            if (!response.ok) throw new Error("manifest");
            manifest = await response.json();
            chunks = [...manifest.chunks].sort(
                (left, right) => Number(left.order) - Number(right.order)
            );
            updateDuration();

            if (desiredLoad) {
                const requestedIndex = chunks.findIndex(
                    (chunk) => Number(chunk.order) === Number(desiredLoad.order)
                );
                if (requestedIndex >= 0 && readyChunk(chunks[requestedIndex])) {
                    const request = desiredLoad;
                    await loadChunk(requestedIndex, request);
                } else if (!manifest.building && requestedIndex < 0) {
                    desiredLoad = null;
                    saveProgress(true, true);
                    setStatus("Leitura concluída");
                }
            }

            if (!initialized && chunks.length) {
                initialized = true;
                const resumeChar = Number(root.dataset.resumeChar || 0);
                const resumeSeconds = Number(root.dataset.resume || 0);
                const index = findChunkForChar(resumeChar);
                const chunk = chunks[index];
                await loadChunk(index, {
                    charOffset: resumeChar > 0 ? resumeChar : null,
                    localTime: chunk?.legacy ? resumeSeconds : null,
                });
            }

            if (manifest.building || desiredLoad) {
                scheduleRefresh(desiredLoad ? 900 : 2500);
            } else {
                clearTimeout(pollTimer);
                pollTimer = null;
            }
        } catch (_) {
            setStatus("Reconectando ao leitor…", true);
            scheduleRefresh(2500);
        }
    };

    function scheduleRefresh(delay) {
        clearTimeout(pollTimer);
        pollTimer = window.setTimeout(refreshManifest, delay);
    }

    const globalPosition = () => {
        const chunk = currentChunk();
        return Number(chunk?.start_seconds || 0) + Number(audio.currentTime || 0);
    };

    const updatePosition = () => {
        const chunk = currentChunk();
        if (!chunk) return;
        const globalSeconds = globalPosition();
        const timing = timingAt(chunk, audio.currentTime);
        showActiveTiming(timing);
        seekRange.value = Math.min(globalSeconds, Number(seekRange.max));
        currentTime.textContent = formatTime(globalSeconds);
    };

    const saveProgress = async (force = false, completed = false) => {
        const now = Date.now();
        if (!force && now - lastSavedAt < 5000) return;
        lastSavedAt = now;
        const chunk = currentChunk();
        const charOffset = Number(
            activeTiming?.char_start ?? chunk?.start_char ?? 0
        );
        try {
            await fetch(root.dataset.progressUrl, {
                method: "PATCH",
                keepalive: true,
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": root.dataset.csrf,
                },
                body: JSON.stringify({
                    position_seconds: globalPosition(),
                    char_offset: charOffset,
                    completed,
                }),
            });
        } catch (_) {
            // O ciclo seguinte tenta salvar novamente.
        }
    };

    const seekGlobal = (seconds, autoplay = !audio.paused) => {
        if (!chunks.length) return;
        const bounded = Math.max(0, Number(seconds));
        const index = findChunkForTime(bounded);
        const chunk = chunks[index];
        const localTime = Math.max(0, bounded - Number(chunk?.start_seconds || 0));
        loadChunk(index, {autoplay, localTime});
    };

    audio.addEventListener("timeupdate", () => {
        updatePosition();
        saveProgress();
    });
    audio.addEventListener("play", () => {
        playButton.innerHTML = '<i class="fa-solid fa-pause"></i>';
        playButton.setAttribute("aria-label", "Pausar");
        root.classList.add("is-playing");
    });
    audio.addEventListener("pause", () => {
        playButton.innerHTML = '<i class="fa-solid fa-play"></i>';
        playButton.setAttribute("aria-label", "Reproduzir");
        root.classList.remove("is-playing");
        if (!switchingSource) saveProgress(true);
    });
    audio.addEventListener("ended", async () => {
        const nextIndex = chunkIndex + 1;
        if (nextIndex < chunks.length) {
            await loadChunk(nextIndex, {autoplay: true, localTime: 0});
            return;
        }
        if (manifest?.building) {
            desiredLoad = {
                order: Number(currentChunk()?.order || 0) + 1,
                autoplay: true,
                localTime: 0,
            };
            setStatus("Aguardando o próximo trecho…", true);
            scheduleRefresh(600);
            return;
        }
        showActiveTiming((currentChunk()?.word_timings || []).at(-1));
        saveProgress(true, true);
        setStatus("Leitura concluída");
    });

    playButton.addEventListener("click", () => {
        if (!audio.src && chunks.length) {
            loadChunk(0, {autoplay: true});
        } else if (audio.paused) {
            audio.play();
        } else {
            audio.pause();
        }
    });
    document.getElementById("backButton").addEventListener("click", () => {
        seekGlobal(globalPosition() - 15);
    });
    document.getElementById("forwardButton").addEventListener("click", () => {
        seekGlobal(globalPosition() + 15);
    });
    seekRange.addEventListener("change", () => {
        seekGlobal(Number(seekRange.value));
    });
    playbackRate.addEventListener("change", () => {
        audio.playbackRate = Number(playbackRate.value);
    });

    const playFromWord = (word) => {
        const charOffset = Number(word.dataset.charStart);
        if (word === activeWord && !audio.paused) {
            audio.pause();
            return;
        }
        loadChunk(findChunkForChar(charOffset), {
            autoplay: true,
            charOffset,
        });
    };
    words.forEach((word) => {
        word.addEventListener("click", () => playFromWord(word));
        word.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                playFromWord(word);
            }
        });
    });

    window.addEventListener("pagehide", () => saveProgress(true));
    refreshManifest();
});
