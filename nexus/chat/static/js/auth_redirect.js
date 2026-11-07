/* Stufe 1.2: bei 401 von der eigenen API einmalig zur Login-Seite (Fernzugriff ohne Session). */
(function () {
    "use strict";

    const origFetch = window.fetch.bind(window);
    let redirecting = false;

    window.fetch = async function (input, init) {
        const res = await origFetch(input, init);

        try {
            const url = new URL(input instanceof Request ? input.url : String(input), location.href);
            if (
                res.status === 401 &&
                url.origin === location.origin &&
                redirecting === false &&
                location.pathname !== "/login"
            ) {
                redirecting = true;
                location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
            }
        } catch (e) {
            /* URL-Parsing fehlerhaft: nichts tun. */
        }

        return res;
    };
})();
