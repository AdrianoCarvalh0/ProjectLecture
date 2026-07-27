(() => {
    const state = {
        pickerReady: false,
        identityReady: false,
        tokenClient: null,
        accessToken: null,
    };

    const elements = () => ({
        button: document.getElementById("googleDriveButton"),
        status: document.getElementById("driveStatus"),
        form: document.getElementById("documentCreateForm"),
    });

    const setStatus = (message, kind = "") => {
        const status = elements().status;
        if (!status) return;
        status.textContent = message;
        status.className = `drive-status ${kind}`.trim();
    };

    const maybeEnable = () => {
        const button = elements().button;
        if (button && state.pickerReady && state.identityReady) {
            button.disabled = false;
            setStatus("Pronto para escolher PDF, DOCX, EPUB, TXT ou Google Docs.");
        }
    };

    const importFile = async (fileId, fileName) => {
        const {button, form} = elements();
        if (!button || !form) return;
        const title = form.querySelector("[name=title]");
        if (title && !title.value.trim()) {
            title.value = fileName.replace(/\.[^.]+$/, "");
        }
        if (!title?.value.trim()) {
            setStatus("Informe um título antes de importar.", "is-error");
            title?.focus();
            return;
        }

        const voice = form.querySelector("[name=voice]:checked");
        if (!voice) {
            setStatus("Escolha uma voz antes de importar.", "is-error");
            return;
        }

        const payload = new FormData();
        payload.append("title", title.value);
        payload.append("voice", voice.value);
        payload.append("reading_mode", form.querySelector("[name=reading_mode]").value);
        payload.append("speed", form.querySelector("[name=speed]").value);
        payload.append("file_id", fileId);
        payload.append("access_token", state.accessToken);

        button.disabled = true;
        setStatus(`Importando “${fileName}”…`, "is-loading");
        try {
            const response = await fetch(button.dataset.importUrl, {
                method: "POST",
                headers: {
                    "Accept": "application/json",
                    "X-CSRFToken": form.querySelector("[name=csrfmiddlewaretoken]").value,
                },
                body: payload,
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || "A importação falhou.");
            window.location.assign(result.redirect_url);
        } catch (error) {
            button.disabled = false;
            setStatus(error.message || "A importação falhou.", "is-error");
        }
    };

    const showPicker = () => {
        const button = elements().button;
        const view = new google.picker.View(google.picker.ViewId.DOCS);
        view.setMimeTypes([
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/epub+zip",
            "text/plain",
            "application/vnd.google-apps.document",
        ].join(","));
        const picker = new google.picker.PickerBuilder()
            .setDeveloperKey(button.dataset.apiKey)
            .setAppId(button.dataset.appId)
            .setOAuthToken(state.accessToken)
            .setOrigin(window.location.origin)
            .addView(view)
            .setCallback((data) => {
                if (data.action !== google.picker.Action.PICKED) return;
                const selected = data[google.picker.Response.DOCUMENTS][0];
                importFile(
                    selected[google.picker.Document.ID],
                    selected[google.picker.Document.NAME] || "documento",
                );
            })
            .build();
        picker.setVisible(true);
    };

    const authorizeAndPick = () => {
        if (!state.tokenClient) {
            setStatus("O seletor do Google ainda está carregando.", "is-error");
            return;
        }
        state.tokenClient.callback = (response) => {
            if (response.error) {
                setStatus("O acesso ao Google Drive não foi autorizado.", "is-error");
                return;
            }
            state.accessToken = response.access_token;
            showPicker();
        };
        state.tokenClient.requestAccessToken({
            prompt: state.accessToken ? "" : "consent",
        });
    };

    window.ProjectLectureDrive = {
        gapiLoaded() {
            gapi.load("picker", () => {
                state.pickerReady = true;
                maybeEnable();
            });
        },
        gisLoaded() {
            const button = elements().button;
            if (!button) return;
            state.tokenClient = google.accounts.oauth2.initTokenClient({
                client_id: button.dataset.clientId,
                scope: "https://www.googleapis.com/auth/drive.file",
                callback: "",
            });
            state.identityReady = true;
            maybeEnable();
        },
    };

    document.addEventListener("DOMContentLoaded", () => {
        const {button, form} = elements();
        button?.addEventListener("click", authorizeAndPick);
        form?.addEventListener("submit", (event) => {
            if (form.dataset.activeSource !== "drive") return;
            event.preventDefault();
            authorizeAndPick();
        });
    });
})();
