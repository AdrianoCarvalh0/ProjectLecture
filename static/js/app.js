window.ProjectLecture = {
    pollDocumentStatus() {
        const processing = document.getElementById("processingState");
        if (!processing) return;
        const interval = window.setInterval(async () => {
            try {
                const response = await fetch(processing.dataset.statusUrl, {
                    headers: {"Accept": "application/json"},
                });
                if (!response.ok) return;
                const documentData = await response.json();
                if (documentData.status === "ready" || documentData.status === "failed") {
                    window.clearInterval(interval);
                    window.location.reload();
                }
            } catch (_) {
                // A próxima tentativa cobre falhas transitórias.
            }
        }, 3000);
    },
};

document.addEventListener("DOMContentLoaded", () => {
    const sidebarToggle = document.getElementById("sidebarToggle");
    sidebarToggle?.addEventListener("click", () => {
        document.body.classList.toggle("sb-sidenav-toggled");
        localStorage.setItem(
            "projectlecture-sidebar",
            document.body.classList.contains("sb-sidenav-toggled") ? "1" : "0",
        );
    });
    if (localStorage.getItem("projectlecture-sidebar") === "1") {
        document.body.classList.add("sb-sidenav-toggled");
    }

    const sourceTabs = document.querySelectorAll(".source-tab");
    const textSource = document.getElementById("textSource");
    const fileSource = document.getElementById("fileSource");
    sourceTabs.forEach((tab) => tab.addEventListener("click", () => {
        sourceTabs.forEach((item) => item.classList.toggle("active", item === tab));
        const showText = tab.dataset.source === "text";
        textSource?.classList.toggle("d-none", !showText);
        fileSource?.classList.toggle("d-none", showText);
        if (showText) {
            const fileInput = fileSource?.querySelector("input[type=file]");
            if (fileInput) fileInput.value = "";
        } else {
            const textarea = textSource?.querySelector("textarea");
            if (textarea) textarea.value = "";
        }
    }));

    const textArea = document.getElementById("id_text");
    const characterCount = document.getElementById("characterCount");
    const updateCharacters = () => {
        if (characterCount && textArea) {
            characterCount.textContent = `${textArea.value.length.toLocaleString("pt-BR")} caracteres`;
        }
    };
    textArea?.addEventListener("input", updateCharacters);
    updateCharacters();

    const fileInput = document.getElementById("id_original_file");
    const selectedFile = document.getElementById("selectedFile");
    fileInput?.addEventListener("change", () => {
        if (selectedFile) {
            selectedFile.innerHTML = fileInput.files.length
                ? `<i class="fa-solid fa-file-circle-check"></i> ${fileInput.files[0].name}`
                : "";
        }
    });

    const speedInput = document.getElementById("id_speed");
    const speedValue = document.getElementById("speedValue");
    speedInput?.setAttribute("type", "range");
    speedInput?.classList.add("form-range");
    speedInput?.addEventListener("input", () => {
        if (speedValue) speedValue.textContent = speedInput.value;
    });

    const voiceOptions = [...document.querySelectorAll(".voice-option")];
    const previewAudio = new Audio();
    let activePreviewButton = null;

    voiceOptions.forEach((option) => {
        const radio = option.querySelector("input[type=radio]");
        radio?.addEventListener("change", () => {
            voiceOptions.forEach((item) => item.classList.toggle(
                "selected",
                item.querySelector("input[type=radio]")?.checked,
            ));
        });
        option.addEventListener("click", (event) => {
            if (event.target.closest(".voice-preview")) return;
            if (radio) {
                radio.checked = true;
                radio.dispatchEvent(new Event("change", {bubbles: true}));
            }
        });
    });

    const resetPreviewButton = () => {
        if (!activePreviewButton) return;
        activePreviewButton.classList.remove("playing", "loading");
        activePreviewButton.innerHTML = '<i class="fa-solid fa-circle-play"></i> Ouvir amostra';
        activePreviewButton = null;
    };
    document.querySelectorAll(".voice-preview").forEach((button) => {
        button.addEventListener("click", async (event) => {
            event.preventDefault();
            event.stopPropagation();
            if (activePreviewButton === button && !previewAudio.paused) {
                previewAudio.pause();
                previewAudio.currentTime = 0;
                resetPreviewButton();
                return;
            }
            previewAudio.pause();
            resetPreviewButton();
            activePreviewButton = button;
            button.classList.add("loading");
            button.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Preparando...';
            previewAudio.src = button.dataset.previewUrl;
            try {
                await previewAudio.play();
                button.classList.remove("loading");
                button.classList.add("playing");
                button.innerHTML = '<i class="fa-solid fa-circle-stop"></i> Parar amostra';
            } catch (_) {
                button.classList.remove("loading");
                button.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i> Tente novamente';
            }
        });
    });
    previewAudio.addEventListener("ended", resetPreviewButton);
});
