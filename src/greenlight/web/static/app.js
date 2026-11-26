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
    advice.hidden = lines.length === 0;
  };

  const ask = async () => {
    const hasQuote = field("kept_quote") || field("quotes").files.length > 0;
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
  field("quotes").addEventListener("change", soon);
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
