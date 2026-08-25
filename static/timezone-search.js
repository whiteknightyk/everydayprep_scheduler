(() => {
  const root = document.querySelector("[data-timezone-search]");
  if (!root) return;

  const language = root.dataset.language === "en" ? "en" : "ja";
  const messages = {
    ja: {
      noResults: "候補が見つかりませんでした。別の地名または国名を追加してお試しください。",
      selected: (location, timezone) => `${location} のタイムゾーンを ${timezone} に設定しました。`,
      results: (count) => `${count}件の候補が見つかりました。所在地を選択してください。`,
      shortQuery: "2文字以上の地名を入力してください。",
      searching: "検索しています…",
      searchError: "地名を検索できませんでした。",
    },
    en: {
      noResults: "No results found. Try adding a different location or country name.",
      selected: (location, timezone) => `Set the timezone for ${location} to ${timezone}.`,
      results: (count) => `Found ${count} result${count === 1 ? "" : "s"}. Select a location.`,
      shortQuery: "Enter at least two characters.",
      searching: "Searching…",
      searchError: "Unable to search for the location.",
    },
  }[language];

  const locationInput = root.querySelector('input[type="search"]');
  const searchButton = root.querySelector('button[type="button"]');
  const status = root.querySelector(".timezone-search-status");
  const results = root.querySelector(".timezone-results");
  const timezoneInput = root.closest("form").querySelector('input[name="timezone_name"]');
  let debounceTimer;
  let requestController;
  let selectedLocation = "";

  const setStatus = (message, state = "") => {
    status.textContent = message;
    status.className = `timezone-search-status${state ? ` ${state}` : ""}`;
  };

  const locationLabel = (item) => {
    const parts = [item.name, item.admin1, item.country].filter(
      (part, index, all) => part && all.indexOf(part) === index,
    );
    return parts.join("、");
  };

  const renderResults = (items) => {
    results.replaceChildren();
    if (!items.length) {
      results.hidden = true;
      setStatus(messages.noResults, "error");
      return;
    }

    for (const item of items) {
      const option = document.createElement("button");
      option.type = "button";
      option.className = "timezone-option";
      option.setAttribute("role", "option");

      const place = document.createElement("span");
      place.textContent = locationLabel(item);
      const timezone = document.createElement("code");
      timezone.textContent = item.timezone;
      option.append(place, timezone);

      option.addEventListener("click", () => {
        selectedLocation = locationLabel(item);
        locationInput.value = selectedLocation;
        timezoneInput.value = item.timezone;
        timezoneInput.dispatchEvent(new Event("change", { bubbles: true }));
        results.hidden = true;
        setStatus(messages.selected(selectedLocation, item.timezone), "selected");
      });
      results.append(option);
    }
    results.hidden = false;
    setStatus(messages.results(items.length));
  };

  const search = async () => {
    const query = locationInput.value.trim();
    if (query.length < 2) {
      if (requestController) requestController.abort();
      results.hidden = true;
      setStatus(messages.shortQuery);
      return;
    }

    if (requestController) requestController.abort();
    const controller = new AbortController();
    requestController = controller;
    searchButton.disabled = true;
    setStatus(messages.searching);

    try {
      const response = await fetch(`/api/timezones/search?q=${encodeURIComponent(query)}`, {
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(language === "ja" ? data.detail || messages.searchError : messages.searchError);
      }
      renderResults(data.results || []);
    } catch (error) {
      if (error.name === "AbortError") return;
      results.hidden = true;
      setStatus(error.message || messages.searchError, "error");
    } finally {
      if (requestController === controller) searchButton.disabled = false;
    }
  };

  locationInput.addEventListener("input", () => {
    if (locationInput.value !== selectedLocation) {
      selectedLocation = "";
      timezoneInput.value = "";
    }
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(search, 400);
  });
  locationInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      clearTimeout(debounceTimer);
      search();
    } else if (event.key === "Escape") {
      results.hidden = true;
    }
  });
  searchButton.addEventListener("click", () => {
    clearTimeout(debounceTimer);
    search();
  });
  document.addEventListener("click", (event) => {
    if (!root.contains(event.target)) results.hidden = true;
  });
})();
