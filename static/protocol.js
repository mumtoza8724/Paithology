// Страница создания протокола: шаги, генерация текстов, заключения,
// редактор фото и скачивание DOCX/PDF без перезагрузки страницы.

(function () {

    const page = document.getElementById("protocol-page");
    const form = document.getElementById("protocol-form");
    const kind = page.dataset.kind;
    const csrf = page.dataset.csrf;

    const HEADER_KEY = "paithology.labHeader";

    // Данные протокола из архива при редактировании
    const initialNode = document.getElementById("protocol-initial");
    const initial = initialNode ? JSON.parse(initialNode.textContent) : null;

    function value(name) {
        const field = form.elements[name];
        if (!field) return "";
        if (field instanceof RadioNodeList) return field.value || "";
        return (field.value || "").trim();
    }

    async function postJSON(url, payload) {
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

    function formatDate(text) {
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
        return m ? `${m[3]}.${m[2]}.${m[1]}` : text;
    }

    // ---------- Шаги ----------

    function showStep(step) {
        document.querySelectorAll("[data-step]").forEach((s) => { s.hidden = s.dataset.step !== String(step); });
        document.querySelectorAll("[data-pill]").forEach((p) => p.classList.toggle("active", p.dataset.pill === String(step)));
        window.scrollTo({ top: 0, behavior: "smooth" });
    }

    document.getElementById("to-step-2").addEventListener("click", () => {
        const status = document.getElementById("step1-status");
        const missing = [];
        if (!value("research_number")) missing.push("№ исследования");
        if (!value("patient_name")) missing.push("ФИО пациента");
        if (missing.length) {
            status.textContent = "Заполните: " + missing.join(", ");
            return;
        }
        status.textContent = "";
        const gender = value("gender");
        document.getElementById("sum-name").textContent = value("patient_name") || "—";
        document.getElementById("sum-gender").textContent = gender === "Мужской" ? "Муж" : gender === "Женский" ? "Жен" : "—";
        document.getElementById("sum-birth").textContent = formatDate(value("birth_date")) || "—";
        showStep(2);
    });

    document.getElementById("to-step-1").addEventListener("click", () => showStep(1));

    // ---------- Макроописание ----------

    const macroNotice = document.getElementById("macro-notice");

    document.getElementById("generate-macro").addEventListener("click", (event) => {
        const button = event.currentTarget;
        const payload = { kind, doc_lang: value("doc_lang") };
        if (kind === "small") {
            Object.assign(payload, {
                size: document.getElementById("m-size").value,
                color: document.getElementById("m-color").value,
                consistency: document.getElementById("m-consistency").value,
                work: document.getElementById("m-work").value,
                archive: document.getElementById("m-archive").value,
            });
        } else {
            Object.assign(payload, {
                source_lang: document.getElementById("source-lang").value,
                notes: document.getElementById("macro-notes").value,
                marking_notes: document.getElementById("marking-notes").value,
            });
        }
        withBusy(button, async () => {
            try {
                const data = await postJSON(page.dataset.macroUrl, payload);
                document.getElementById("macro-text").value = data.text || "";
                const marking = document.getElementById("marking-text");
                if (marking) marking.value = data.marking || "";
                macroNotice.textContent = data.notice || "";
            } catch (error) {
                macroNotice.textContent = error.message;
            }
        });
    });

    // ---------- Заключения ----------

    const list = document.getElementById("conclusions");
    const template = document.getElementById("conclusion-template");
    let counter = 0;

    function renumber() {
        list.querySelectorAll("[data-conclusion]").forEach((card, i) => {
            card.querySelector("[data-number]").textContent = i + 1;
            card.querySelector("[data-remove]").hidden = list.children.length === 1;
        });
    }

    function addConclusion(data) {
        const index = counter++;
        const card = template.content.firstElementChild.cloneNode(true);
        card.querySelectorAll("[data-field]").forEach((field) => {
            field.name = `c-${index}-${field.dataset.field}`;
        });
        card.querySelector("[data-remove]").addEventListener("click", () => { card.remove(); renumber(); });

        const button = card.querySelector("[data-generate]");
        button.addEventListener("click", () => withBusy(button, async () => {
            const notice = card.querySelector("[data-notice]");
            const diagnosis = card.querySelector("[data-field=diagnosis]").value.trim();
            if (!diagnosis) { notice.textContent = "Сначала введите заключение."; return; }
            try {
                const data = await postJSON(page.dataset.microUrl, { diagnosis, doc_lang: value("doc_lang") });
                if (data.micro) card.querySelector("[data-field=micro]").value = data.micro;
                const icdo = card.querySelector("[data-field=icdo]");
                if (data.icdo) icdo.value = data.icdo;
                notice.textContent = data.notice || "";
            } catch (error) {
                notice.textContent = error.message;
            }
        }));

        if (data) {
            card.querySelector("[data-field=diagnosis]").value = data.diagnosis || "";
            card.querySelector("[data-field=micro]").value = data.micro || "";
            card.querySelector("[data-field=icdo]").value = data.icdo || "";
        }

        setupPhotoSlot(card.querySelector("[data-photo-slot]"), data && data.photo);
        list.appendChild(card);
        renumber();
    }

    document.getElementById("add-conclusion").addEventListener("click", () => addConclusion());

    // ---------- Фото и редактор ----------

    const editor = document.getElementById("photo-editor");
    const canvas = document.getElementById("editor-canvas");
    const ctx = canvas.getContext("2d");
    const state = { image: null, rotation: 0, crop: null, drag: null, scale: 1, resolve: null };

    function rotatedSource() {
        const img = state.image;
        const turned = state.rotation % 180 !== 0;
        const source = document.createElement("canvas");
        source.width = turned ? img.naturalHeight : img.naturalWidth;
        source.height = turned ? img.naturalWidth : img.naturalHeight;
        const c = source.getContext("2d");
        c.translate(source.width / 2, source.height / 2);
        c.rotate((state.rotation * Math.PI) / 180);
        c.drawImage(img, -img.naturalWidth / 2, -img.naturalHeight / 2);
        return source;
    }

    function redraw() {
        const source = rotatedSource();
        const maxW = Math.min(880, window.innerWidth - 120);
        const maxH = Math.max(240, window.innerHeight - 320);
        state.scale = Math.min(maxW / source.width, maxH / source.height, 1);
        canvas.width = Math.round(source.width * state.scale);
        canvas.height = Math.round(source.height * state.scale);
        ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
        if (state.crop) {
            const { x, y, w, h } = state.crop;
            ctx.save();
            ctx.fillStyle = "rgba(5, 5, 20, .55)";
            ctx.fillRect(0, 0, canvas.width, y);
            ctx.fillRect(0, y + h, canvas.width, canvas.height - y - h);
            ctx.fillRect(0, y, x, h);
            ctx.fillRect(x + w, y, canvas.width - x - w, h);
            ctx.strokeStyle = "#c084fc";
            ctx.lineWidth = 2;
            ctx.setLineDash([8, 6]);
            ctx.strokeRect(x, y, w, h);
            ctx.restore();
        }
    }

    function pointer(event) {
        const rect = canvas.getBoundingClientRect();
        return {
            x: Math.max(0, Math.min(canvas.width, ((event.clientX - rect.left) * canvas.width) / rect.width)),
            y: Math.max(0, Math.min(canvas.height, ((event.clientY - rect.top) * canvas.height) / rect.height)),
        };
    }

    canvas.addEventListener("pointerdown", (event) => {
        canvas.setPointerCapture(event.pointerId);
        state.drag = pointer(event);
        state.crop = null;
    });

    canvas.addEventListener("pointermove", (event) => {
        if (!state.drag) return;
        const p = pointer(event);
        state.crop = {
            x: Math.min(p.x, state.drag.x), y: Math.min(p.y, state.drag.y),
            w: Math.abs(p.x - state.drag.x), h: Math.abs(p.y - state.drag.y),
        };
        redraw();
    });

    canvas.addEventListener("pointerup", () => {
        state.drag = null;
        if (state.crop && (state.crop.w < 10 || state.crop.h < 10)) state.crop = null;
        redraw();
    });

    editor.querySelectorAll("[data-rotate]").forEach((button) => button.addEventListener("click", () => {
        state.rotation = (state.rotation + Number(button.dataset.rotate) + 360) % 360;
        state.crop = null;
        redraw();
    }));

    document.getElementById("editor-reset").addEventListener("click", () => { state.crop = null; redraw(); });

    function closeEditor(result) {
        editor.hidden = true;
        if (state.resolve) state.resolve(result);
        state.resolve = null;
    }

    document.getElementById("editor-cancel").addEventListener("click", () => closeEditor(null));

    document.getElementById("editor-apply").addEventListener("click", () => {
        const source = rotatedSource();
        const crop = state.crop
            ? { x: state.crop.x / state.scale, y: state.crop.y / state.scale, w: state.crop.w / state.scale, h: state.crop.h / state.scale }
            : { x: 0, y: 0, w: source.width, h: source.height };
        const out = document.createElement("canvas");
        out.width = Math.round(crop.w);
        out.height = Math.round(crop.h);
        out.getContext("2d").drawImage(source, crop.x, crop.y, crop.w, crop.h, 0, 0, out.width, out.height);
        out.toBlob((blob) => closeEditor(blob), "image/jpeg", 0.92);
    });

    function openEditor(file) {
        return new Promise((resolve) => {
            const img = new Image();
            img.onload = () => {
                Object.assign(state, { image: img, rotation: 0, crop: null, drag: null, resolve });
                editor.hidden = false;
                redraw();
                URL.revokeObjectURL(img.src);
            };
            img.onerror = () => resolve(null);
            img.src = URL.createObjectURL(file);
        });
    }

    function setupPhotoSlot(slot, existing) {
        const input = slot.querySelector("input[type=file]");
        const existingInput = slot.querySelector("[data-photo-existing]");
        const drop = slot.querySelector("[data-photo-drop]");
        const buttons = slot.querySelector("[data-photo-buttons]");
        const placeholder = drop.innerHTML;
        let original = null;
        let existingUrl = null;

        function showPreview(src) {
            drop.innerHTML = "";
            const img = document.createElement("img");
            img.src = src;
            drop.appendChild(img);
            buttons.hidden = false;
        }

        // Уже сохранённое в архиве фото: остаётся, пока его не заменят или не удалят
        if (existing && existing.name) {
            existingInput.value = existing.name;
            existingUrl = existing.url;
            showPreview(existing.url);
        }

        function setFile(blob) {
            if (blob) {
                existingInput.value = "";
                existingUrl = null;
            }
            const transfer = new DataTransfer();
            if (blob) transfer.items.add(new File([blob], "photo.jpg", { type: "image/jpeg" }));
            input.files = transfer.files;
            if (blob) {
                drop.innerHTML = "";
                const img = document.createElement("img");
                img.src = URL.createObjectURL(blob);
                drop.appendChild(img);
            } else {
                drop.innerHTML = placeholder;
            }
            buttons.hidden = !blob;
        }

        async function edit(file) {
            const result = await openEditor(file);
            if (result) setFile(result);
            else if (!input.files.length) setFile(null);
        }

        drop.addEventListener("click", () => input.click());

        input.addEventListener("change", () => {
            const file = input.files[0];
            if (!file) return;
            original = file;
            edit(file);
        });

        slot.querySelector("[data-photo-edit]").addEventListener("click", async () => {
            if (original || input.files[0]) return edit(original || input.files[0]);
            if (existingUrl) {
                const blob = await (await fetch(existingUrl)).blob();
                original = new File([blob], "photo.jpg", { type: blob.type });
                edit(original);
            }
        });
        slot.querySelector("[data-photo-remove]").addEventListener("click", () => {
            original = null;
            existingInput.value = "";
            existingUrl = null;
            setFile(null);
        });
    }

    document.querySelectorAll(".macro-photo[data-photo-slot]").forEach((slot) => {
        setupPhotoSlot(slot, initial && initial.macro_photo);
    });

    // ---------- Заполнение из архива ----------

    function setValue(name, text) {
        const field = form.elements[name];
        if (!field || text === undefined || text === null) return;
        if (field instanceof RadioNodeList) { field.value = text; return; }
        if (/^\d{4}-\d{2}-\d{2}$/.test(text) && field.getAttribute("onfocus")) field.type = "date";
        field.value = text;
    }

    if (initial) {
        Object.entries(initial.fields || {}).forEach(([name, text]) => setValue(name, text));
        Object.entries(initial.macro_inputs || {}).forEach(([name, text]) => setValue(name, text));
        setValue("doc_lang", initial.lang);
        setValue("macro_text", initial.macro_text);
        setValue("marking_text", initial.marking_text);
        setValue("consultant_title", initial.consultant_title);
        setValue("consultant_name", initial.consultant_name);
        (initial.conclusions || []).forEach((c) => addConclusion(c));
    }

    if (!list.children.length) addConclusion();

    // ---------- Шапка лаборатории (хранится в браузере) ----------

    function readHeader() {
        try { return JSON.parse(localStorage.getItem(HEADER_KEY) || "null"); } catch (e) { return null; }
    }

    const chip = document.getElementById("header-chip");
    const header = readHeader();
    const configured = header && (header.org || header.form || header.address || header.logo);
    chip.className = "header-chip " + (configured ? "ok" : "missing");
    chip.innerHTML = configured
        ? '<i class="bi bi-check-circle"></i> Шапка лаборатории настроена'
        : '<i class="bi bi-exclamation-circle"></i> Шапка лаборатории не настроена — открыть настройки';

    async function dataUrlToBlob(url) {
        return (await fetch(url)).blob();
    }

    // ---------- Скачивание ----------

    const downloadStatus = document.getElementById("download-status");
    const protocolId = document.getElementById("protocol-id");

    async function collect() {
        const data = new FormData(form);
        const h = readHeader() || {};
        data.set("header_org", h.org || "");
        data.set("header_form", h.form || "");
        data.set("header_address", h.address || "");
        if (h.logo) data.set("header_logo", await dataUrlToBlob(h.logo), "logo.png");
        return data;
    }

    // После первого сохранения форма продолжает работать с тем же протоколом
    function remember(id, editUrl) {
        if (!id) return;
        protocolId.value = id;
        history.replaceState(null, "", editUrl || `/protocols/${id}/edit`);
    }

    function savedMessage(id, prefix) {
        downloadStatus.className = "status-line";
        downloadStatus.innerHTML = "";
        downloadStatus.append(prefix + " ");
        const link = document.createElement("a");
        link.href = `/protocols/${id}`;
        link.textContent = "Открыть в архиве";
        link.style.color = "#d8b4fe";
        downloadStatus.append(link);
    }

    const saveButton = document.getElementById("save-protocol");

    saveButton.addEventListener("click", () => withBusy(saveButton, async () => {
        downloadStatus.className = "status-line";
        downloadStatus.textContent = "Сохраняю…";
        try {
            const response = await fetch(page.dataset.saveUrl, {
                method: "POST", body: await collect(), headers: { "X-CSRF-Token": csrf },
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(result.error || "Не удалось сохранить протокол");
            remember(result.id, result.edit_url);
            savedMessage(result.id, result.changed ? "Сохранено в архив." : "Изменений нет, протокол уже в архиве.");
        } catch (error) {
            downloadStatus.className = "status-line error";
            downloadStatus.textContent = error.message;
        }
    }));

    document.querySelectorAll("[data-download]").forEach((button) => button.addEventListener("click", () => withBusy(button, async () => {
        const format = button.dataset.download;
        const data = await collect();
        data.set("format", format);

        downloadStatus.className = "status-line";
        downloadStatus.textContent = "Собираю документ…";

        try {
            const response = await fetch(page.dataset.downloadUrl, {
                method: "POST", body: data, headers: { "X-CSRF-Token": csrf },
            });
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.error || "Не удалось собрать документ");
            }
            const blob = await response.blob();
            const disposition = response.headers.get("Content-Disposition") || "";
            // Сначала полное имя в UTF-8 (filename*), затем запасное ASCII-имя
            const match = /filename\*=UTF-8''([^;]+)/i.exec(disposition) || /filename="?([^";]+)"?/i.exec(disposition);
            const link = document.createElement("a");
            link.href = URL.createObjectURL(blob);
            link.download = match ? decodeURIComponent(match[1]) : `protocol.${format}`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(link.href), 10000);
            const id = response.headers.get("X-Protocol-Id");
            remember(id);
            if (id) savedMessage(id, "Документ скачан и сохранён в архив.");
        } catch (error) {
            downloadStatus.className = "status-line error";
            downloadStatus.textContent = error.message;
        }
    })));

})();
