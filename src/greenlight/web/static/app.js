const announcement = document.querySelector("[data-announce]");
if (announcement) {
  const message = announcement.textContent;
  announcement.textContent = "";
  setTimeout(() => {
    announcement.textContent = message;
  }, 250);
}

document.addEventListener("click", (event) => {
  const opener = event.target.closest("[data-open]");
  if (opener) {
    const dialog = document.getElementById(opener.dataset.open);
    if (dialog && typeof dialog.showModal === "function") {
      event.preventDefault();
      dialog.showModal();
      dialog.querySelector("textarea, input:not([type=hidden])")?.focus();
    }
    return;
  }

  const closer = event.target.closest("[data-close]");
  if (closer) {
    closer.closest("dialog")?.close();
    return;
  }

  if (event.target instanceof HTMLDialogElement) {
    event.target.close();
  }
});

document.addEventListener("submit", (event) => {
  const button = event.submitter;
  if (button && button.dataset.busy) {
    button.disabled = true;
    button.textContent = button.dataset.busy;
  }
});

const requestForm = document.querySelector("form.request-form");
if (requestForm) {
  const advice = document.getElementById("advice");
  const field = (name) => requestForm.elements.namedItem(name);
  let timer;

  const say = (lines) => {
    advice.replaceChildren(
      ...lines.map(([kind, text]) => {
        const line = document.createElement("p");
        line.className = kind;
        line.textContent = text;
        return line;
      }),
    );
  };

  const ask = async () => {
    const hasQuote = field("kept_quote") || field("quotes")?.files.length > 0;
    const params = new URLSearchParams({
      supplier: field("supplier").value,
      category_id: field("category_id").value,
      quote: hasQuote ? "yes" : "no",
    });
    const response = await fetch(`/requests/advice?${params}`, { headers: { Accept: "application/json" } });
    if (!response.ok) return;
    const found = await response.json();
    const lines = [];
    if (found.learned_from && !field("category_id").value) {
      field("category_id").value = found.category_id;
      if (found.tag_id && !field("tag_id").value) field("tag_id").value = found.tag_id;
      lines.push(["learned", `Category and project filled in the way ${found.learned_from}, the last request to this supplier, was filed.`]);
    }
    for (const lesson of found.lessons) lines.push(["lesson", lesson]);
    say(lines);
  };

  const soon = () => {
    clearTimeout(timer);
    timer = setTimeout(ask, 400);
  };
  field("supplier").addEventListener("input", soon);
  field("category_id").addEventListener("change", soon);
  field("quotes")?.addEventListener("change", soon);

  const rateNotes = requestForm.querySelectorAll(".rate-note");
  field("currency").addEventListener("change", (event) => {
    for (const note of rateNotes) note.hidden = note.dataset.currency !== event.target.value;
  });
}

for (const drop of document.querySelectorAll("[data-drop]")) {
  const input = drop.querySelector("input[type=file]");
  const fileName = drop.querySelector("[data-file-name]");
  const hint = fileName.textContent;
  input.addEventListener("change", () => {
    const names = [...input.files].map((file) => file.name);
    fileName.textContent = names.length ? names.join(", ") : hint;
    drop.classList.toggle("chosen", names.length > 0);
  });
  input.addEventListener("dragenter", () => drop.classList.add("dragging"));
  for (const type of ["dragleave", "drop"]) {
    input.addEventListener(type, () => drop.classList.remove("dragging"));
  }
}

const quoteReader = document.querySelector("form.quote-reader");
if (quoteReader) {
  const button = quoteReader.querySelector("button[type=submit]");
  const label = button.lastChild.textContent;
  quoteReader.elements.namedItem("confidential").addEventListener("change", (event) => {
    button.lastChild.textContent = event.target.checked ? "Attach without AI" : label;
    button.dataset.busy = event.target.checked ? "Attaching…" : "Reading the quote…";
  });
}

document.addEventListener("click", (event) => {
  const start = event.target.closest("[data-reason]");
  if (!start) return;
  const reason = start.closest("form").elements.namedItem("reason");
  reason.value = start.dataset.reason;
  reason.focus();
  reason.setSelectionRange(reason.value.length, reason.value.length);
});

const card = document.querySelector("[data-swipe]");
if (card) {
  const approveForm = document.querySelector("#decision form");
  const sendBack = document.querySelector("#decision [data-open='reject-sheet']");
  const calm = window.matchMedia("(prefers-reduced-motion: reduce)");
  const flick = 0.5;
  const far = 120;
  let startX = null;
  let startedAt = 0;
  let dx = 0;

  const settle = () => {
    card.style.transition = "";
    card.style.transform = "";
    card.style.removeProperty("--verdict-opacity");
    card.removeAttribute("data-verdict");
  };

  approveForm.addEventListener("submit", (event) => {
    if (calm.matches) return;
    event.preventDefault();
    card.style.transition = "";
    card.style.transform = "";
    card.classList.add("leaving");
    setTimeout(() => approveForm.submit(), 200);
  });

  card.addEventListener("pointerdown", (event) => {
    if (startX !== null || event.target.closest("a, button")) return;
    startX = event.clientX;
    startedAt = event.timeStamp;
    card.setPointerCapture(event.pointerId);
    card.style.transition = "none";
  });
  card.addEventListener("pointermove", (event) => {
    if (startX === null) return;
    dx = event.clientX - startX;
    card.style.transform = `translateX(${dx}px) rotate(${dx / 24}deg)`;
    card.dataset.verdict = dx > 0 ? "Approve" : "Send back";
    card.style.setProperty("--verdict-opacity", Math.min(Math.abs(dx) / far, 1));
  });
  card.addEventListener("pointerup", (event) => {
    if (startX === null) return;
    const decided = Math.abs(dx) > far || (Math.abs(dx) > 40 && Math.abs(dx) / (event.timeStamp - startedAt) > flick);
    startX = null;
    if (decided && dx > 0) {
      approveForm.requestSubmit();
    } else {
      settle();
      if (decided) sendBack.click();
    }
    dx = 0;
  });
  card.addEventListener("pointercancel", () => {
    startX = null;
    dx = 0;
    settle();
  });
}
