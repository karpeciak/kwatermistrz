document.addEventListener("click", function (event) {
  const button = event.target.closest("button[data-miejscowosc]");
  if (!button) return;
  const input = document.getElementById("miejsce-docelowe");
  input.value = button.dataset.miejscowosc;
  input.dispatchEvent(new Event("change", { bubbles: true }));
  document.getElementById("wyniki-miejscowosci").replaceChildren();
  input.focus();
});
