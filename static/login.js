"use strict";
// Ausgelagert statt inline, damit die Content-Security-Policy keine Inline-Skripte erlauben muss.
const msg = document.getElementById("msg");
if (location.search.includes("falsch")) msg.textContent = "Das Passwort stimmt nicht.";
if (location.search.includes("zu-viele")) msg.textContent = "Zu viele Versuche, bitte in 15 Minuten nochmal.";
