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
