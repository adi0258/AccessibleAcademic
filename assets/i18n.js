(function (global) {
  const FALLBACK_TRANSLATIONS = {
    en: { meta: { lang: "en", dir: "ltr" } },
    he: { meta: { lang: "he", dir: "rtl" } }
  };

  const state = {
    initPromise: null
  };

  function detectLocale() {
    const language = (global.navigator.language || global.navigator.userLanguage || "en").toLowerCase();
    return language.startsWith("he") ? "he" : "en";
  }

  function getNestedValue(obj, path) {
    return path.split(".").reduce(function (value, segment) {
      if (value && Object.prototype.hasOwnProperty.call(value, segment)) {
        return value[segment];
      }
      return undefined;
    }, obj);
  }

  function interpolate(text, params) {
    return String(text).replace(/\{(\w+)\}/g, function (match, key) {
      if (params && params[key] != null) {
        return params[key];
      }
      return match;
    });
  }

  function applyDocumentLocale(locale, bundle) {
    const meta = (bundle && bundle.meta) || {};
    document.documentElement.lang = meta.lang || locale;
    document.documentElement.dir = meta.dir || (locale === "he" ? "rtl" : "ltr");
  }

  function createTranslator(bundle, fallbackBundle) {
    return function translate(key, params) {
      const value = getNestedValue(bundle, key) ?? getNestedValue(fallbackBundle, key) ?? key;
      if (typeof value === "string") {
        return interpolate(value, params);
      }
      return value;
    };
  }

  function applyTranslations(root, translate) {
    const container = root || document;

    container.querySelectorAll("[data-i18n]").forEach(function (element) {
      element.textContent = translate(element.getAttribute("data-i18n"));
    });

    container.querySelectorAll("[data-i18n-placeholder]").forEach(function (element) {
      element.setAttribute("placeholder", translate(element.getAttribute("data-i18n-placeholder")));
    });

    container.querySelectorAll("[data-i18n-title]").forEach(function (element) {
      element.setAttribute("title", translate(element.getAttribute("data-i18n-title")));
    });

    container.querySelectorAll("[data-i18n-alt]").forEach(function (element) {
      element.setAttribute("alt", translate(element.getAttribute("data-i18n-alt")));
    });
  }

  async function loadTranslations() {
    const response = await fetch("/assets/translations.json", { cache: "no-store" });
    if (!response.ok) {
      throw new Error("Failed to load translations.json");
    }
    return response.json();
  }

  function init() {
    if (!state.initPromise) {
      state.initPromise = (async function () {
        const locale = detectLocale();
        let translations = FALLBACK_TRANSLATIONS;

        try {
          translations = await loadTranslations();
        } catch (error) {
          console.error("Failed to initialize translations", error);
        }

        const fallbackBundle = translations.en || FALLBACK_TRANSLATIONS.en;
        const bundle = translations[locale] || fallbackBundle;
        const translate = createTranslator(bundle, fallbackBundle);

        applyDocumentLocale(locale, bundle);

        return {
          locale: locale,
          t: translate,
          applyTranslations: function (root) {
            applyTranslations(root, translate);
          }
        };
      })();
    }

    return state.initPromise;
  }

  global.AppI18n = {
    detectLocale: detectLocale,
    init: init
  };
})(window);
