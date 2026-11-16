// Общие поля протокола для страницы протокола и главной страницы:
// календарь, клинический диагноз, несколько макроописаний, значения по
// умолчанию и сохранение файлов в выбранную папку компьютера.

window.ProtocolFields = (function () {

    const DEFAULTS_KEY = "paithology.defaults";
    const DEFAULT_FIELDS = ["lab_worker", "stain_method", "consultant_title", "consultant_name"];

    async function postJSON(url, csrf, payload) {
        const response = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
            body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || "Не удалось выполнить запрос");
        return data;
    }

    async function withBusy(button, task) {
        button.disabled = true;
        try { await task(); } finally { button.disabled = false; }
    }

    // ---------- Календарь: дата выбирается, но можно ввести и год или возраст ----------

    function setupCalendars(root) {
        root.querySelectorAll(".calendar-group").forEach((group) => {
            const text = group.querySelector("[data-calendar-text]");
            const proxy = group.querySelector("[data-calendar-proxy]");
            group.querySelector("[data-calendar-open]").addEventListener("click", () => {
                const m = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(text.value.trim());
                if (m) proxy.value = `${m[3]}-${m[2]}-${m[1]}`;
                if (proxy.showPicker) {
                    try { proxy.showPicker(); return; } catch (e) {}
                }
                proxy.focus();
                proxy.click();
            });
            proxy.addEventListener("change", () => {
                const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(proxy.value);
                if (m) text.value = `${m[3]}.${m[2]}.${m[1]}`;
            });
        });
    }

    // ---------- Клинический диагноз ----------

    function setupClinical(root, options) {
        const block = root.querySelector(".clinical-block");
        if (!block) return;
        const input = block.querySelector("[data-clinical-input]");
        const notice = block.querySelector("[data-clinical-notice]");
        const button = block.querySelector("[data-clinical-generate]");
        button.addEventListener("click", () => withBusy(button, async () => {
            if (!input.value.trim()) { notice.textContent = "Сначала введите клинический диагноз."; return; }
            try {
                const data = await postJSON(options.url, options.csrf, {
                    text: input.value,
                    source_lang: block.querySelector("[data-clinical-lang]").value,
                    doc_lang: options.docLang(),
                });
                input.value = data.text || input.value;
                notice.textContent = data.notice || "";
            } catch (error) {
                notice.textContent = error.message;
            }
        }));
    }

    // ---------- Несколько макроописаний ----------

    function numberedCount(text) {
        return (text || "").split("\n").filter((line) => /^\s*\d+[.)]/.test(line)).length;
    }

    function setupMacroBlocks(root, options) {
        const container = root.querySelector("[data-macro-blocks]");
        if (!container) return null;
        const kind = container.dataset.kind;
        const list = container.querySelector("[data-macro-list]");
        const template = container.querySelector("[data-macro-template]");
        let counter = 0;

        function blocks() {
            return Array.from(list.querySelectorAll("[data-macro-block]"));
        }

        function renumber() {
            blocks().forEach((block, i) => {
                block.querySelector("[data-number]").textContent = i + 1;
                block.querySelector("[data-remove]").hidden = blocks().length === 1;
            });
        }

        function addBlock(data) {
            const index = counter++;
            const block = template.content.firstElementChild.cloneNode(true);
            block.querySelectorAll("[data-field]").forEach((field) => {
                field.name = `mb-${index}-${field.dataset.field}`;
                if (data && data[field.dataset.field] !== undefined && data[field.dataset.field] !== "") {
                    field.value = data[field.dataset.field];
                }
            });
            block.querySelector("[data-remove]").addEventListener("click", () => { block.remove(); renumber(); });

            const button = block.querySelector("[data-generate]");
            button.addEventListener("click", () => withBusy(button, async () => {
                const position = blocks().indexOf(block);
                // Нумерация объектов продолжается после предыдущих макроописаний
                const start = 1 + blocks().slice(0, position)
                    .reduce((sum, b) => sum + numberedCount(b.querySelector("[data-field=text]").value), 0);
                const payload = { kind, doc_lang: options.docLang(), start, intro: position === 0, block: {} };
                block.querySelectorAll("[data-field]").forEach((field) => {
                    if (field.dataset.field !== "text") payload.block[field.dataset.field] = field.value;
                });
                const notice = block.querySelector("[data-notice]");
                try {
                    const result = await postJSON(options.url, options.csrf, payload);
                    block.querySelector("[data-field=text]").value = result.text || "";
                    notice.textContent = result.notice || "";
                } catch (error) {
                    notice.textContent = error.message;
                }
            }));

            list.appendChild(block);
            renumber();
            return block;
        }

        container.querySelector("[data-add-macro]").addEventListener("click", () => addBlock());

        (options.initial || []).forEach((data) => addBlock(data));
        if (!blocks().length) addBlock();

        return { addBlock, blocks };
    }

    // ---------- Значения по умолчанию ----------

    function readDefaults() {
        try { return JSON.parse(localStorage.getItem(DEFAULTS_KEY) || "{}"); } catch (e) { return {}; }
    }

    function writeDefaults(values) {
        try {
            localStorage.setItem(DEFAULTS_KEY, JSON.stringify({ ...readDefaults(), ...values }));
            return true;
        } catch (e) {
            return false;
        }
    }

    function applyDefaults(form) {
        const values = readDefaults();
        DEFAULT_FIELDS.forEach((name) => {
            const field = form.elements[name];
            if (field && !field.value && values[name]) field.value = values[name];
        });
    }

    function rememberDefaults(form) {
        const values = {};
        DEFAULT_FIELDS.forEach((name) => {
            const field = form.elements[name];
            if (field) values[name] = field.value.trim();
        });
        return writeDefaults(values);
    }

    // ---------- Папка для сохранения файлов (Chrome, Edge на компьютере) ----------

    const folderSupported = "showDirectoryPicker" in window;

    function openDb() {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open("paithology", 1);
            request.onupgradeneeded = () => request.result.createObjectStore("handles");
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }

    async function folderStore(mode, action) {
        const db = await openDb();
        return new Promise((resolve, reject) => {
            const tx = db.transaction("handles", mode);
            const request = action(tx.objectStore("handles"));
            tx.oncomplete = () => resolve(request && request.result);
            tx.onerror = () => reject(tx.error);
        });
    }

    async function getFolder() {
        if (!folderSupported) return null;
        try { return await folderStore("readonly", (s) => s.get("protocolFolder")) || null; } catch (e) { return null; }
    }

    async function chooseFolder() {
        const handle = await window.showDirectoryPicker({ id: "paithology-protocols", mode: "readwrite" });
        await folderStore("readwrite", (s) => s.put(handle, "protocolFolder"));
        return handle;
    }

    async function forgetFolder() {
        try { await folderStore("readwrite", (s) => s.delete("protocolFolder")); } catch (e) {}
    }

    // Разрешение на запись нужно запросить сразу по нажатию кнопки (до загрузки файла)
    async function folderReady() {
        if (!readDefaults().autosave) return null;
        const handle = await getFolder();
        if (!handle) return null;
        try {
            let state = await handle.queryPermission({ mode: "readwrite" });
            if (state !== "granted") state = await handle.requestPermission({ mode: "readwrite" });
            return state === "granted" ? handle : null;
        } catch (e) {
            return null;
        }
    }

    async function saveToFolder(handle, name, blob) {
        const file = await handle.getFileHandle(name, { create: true });
        const writable = await file.createWritable();
        await writable.write(blob);
        await writable.close();
    }

    // Имя файла «ФИО номер.docx», как на сервере
    function fileName(patient, number, extension) {
        let name = [patient, number].map((p) => (p || "").trim()).filter(Boolean).join(" ");
        name = name.replace(/[\\/]+/g, "-").replace(/[:*?"<>|\u0000-\u001f]+/g, "").replace(/\s+/g, " ");
        name = name.replace(/^[\s.]+|[\s.]+$/g, "").slice(0, 120);
        return `${name || "protocol"}.${extension}`;
    }

    // Скачать файл: в выбранную папку, если она настроена, иначе обычной загрузкой
    async function deliver(blob, name, folder) {
        if (folder) {
            try {
                await saveToFolder(folder, name, blob);
                return `Файл «${name}» сохранён в папку «${folder.name}».`;
            } catch (e) {
                // Не удалось записать в папку — скачиваем обычным способом
            }
        }
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = name;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(link.href), 10000);
        return `Файл «${name}» скачан.`;
    }

    // ---------- Отчёт по исследованию ИИ-анализа в формате протокола ----------

    function readHeader() {
        try { return JSON.parse(localStorage.getItem("paithology.labHeader") || "{}") || {}; } catch (e) { return {}; }
    }

    function nameFromDisposition(disposition, fallback) {
        const full = /filename\*=UTF-8''([^;]+)/i.exec(disposition || "");
        if (full) {
            try { return decodeURIComponent(full[1]); } catch (e) {}
        }
        return fallback;
    }

    async function downloadCaseReport(url, csrf, format) {
        const folder = await folderReady();
        const data = new FormData();
        const header = readHeader();
        const defaults = readDefaults();
        data.set("csrf_token", csrf);
        data.set("format", format);
        data.set("header_org", header.org || "");
        data.set("header_form", header.form || "");
        data.set("header_address", header.address || "");
        data.set("consultant_title", defaults.consultant_title || "");
        data.set("consultant_name", defaults.consultant_name || "");
        if (header.logo) data.set("header_logo", await (await fetch(header.logo)).blob(), "logo.png");
        const response = await fetch(url, { method: "POST", body: data, headers: { "X-CSRF-Token": csrf } });
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.error || "Не удалось собрать отчёт");
        }
        const name = nameFromDisposition(response.headers.get("Content-Disposition"), `report.${format}`);
        return deliver(await response.blob(), name, folder);
    }

    // Кнопки [data-case-report="docx|pdf"] на странице результата и в карточке исследования
    function setupCaseReport(root) {
        const status = root.querySelector("[data-case-report-status]");
        root.querySelectorAll("[data-case-report]").forEach((button) => button.addEventListener("click", () => withBusy(button, async () => {
            if (status) status.textContent = "Собираю отчёт…";
            try {
                const message = await downloadCaseReport(button.dataset.url, button.dataset.csrf, button.dataset.caseReport);
                if (status) status.textContent = message;
            } catch (error) {
                if (status) status.textContent = error.message;
            }
        })));
    }

    return {
        postJSON, withBusy, setupCalendars, setupClinical, setupMacroBlocks, setupCaseReport,
        readDefaults, writeDefaults, applyDefaults, rememberDefaults,
        folderSupported, getFolder, chooseFolder, forgetFolder, folderReady, deliver, fileName,
    };

})();
