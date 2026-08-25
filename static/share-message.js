(() => {
  "use strict";

  const copyWithFallback = async (textarea) => {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(textarea.value);
      return;
    }

    textarea.focus();
    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);
    if (!document.execCommand("copy")) {
      throw new Error("copy command failed");
    }
  };

  document.querySelectorAll("[data-copy-message]").forEach((button) => {
    button.addEventListener("click", async () => {
      const textarea = document.getElementById(button.dataset.target);
      const container = button.closest("[data-share-message]");
      const status = container?.querySelector("[data-copy-status]");
      if (!textarea || !status) return;

      try {
        await copyWithFallback(textarea);
        status.textContent = button.dataset.success || "Copied";
        status.classList.remove("error");
      } catch (_error) {
        status.textContent = button.dataset.error || "Copy failed";
        status.classList.add("error");
        textarea.focus();
        textarea.select();
      }
    });
  });
})();
