const PASTE = document.getElementById("button-paste");
const URL = document.getElementById("url")

PASTE.addEventListener("click", async () => {
    URL.value = await navigator.clipboard.readText();
});