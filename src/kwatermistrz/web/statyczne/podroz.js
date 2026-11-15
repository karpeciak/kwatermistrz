(() => {
  const form = document.getElementById("wybor-podrozy");
  if (!form) return;
  const summary = document.getElementById("podsumowanie-wyboru");
  const confirm = document.getElementById("zatwierdz-przejazdy");
  const consentLabel = document.getElementById("zgoda-transport");
  const consent = form.elements.zgoda_na_zmiane_transportu;
  const reason = form.elements.uzasadnienie;
  if (!reason) return;
  const money = new Intl.NumberFormat("pl-PL", { style: "currency", currency: "PLN" });
  const selected = name => form.querySelector(`input[name="${name}"]:checked`);
  const refresh = () => {
    const there = selected("id_tam");
    const back = selected("id_powrot");
    const changed = [there, back].some(x => x && x.dataset.zgodny === "false");
    consentLabel.hidden = !changed;
    if (!changed) consent.checked = false;
    if (!summary || !confirm) return;
    let message = "Wybierz przejazd tam i z powrotem.";
    let valid = false;
    if (there && back) {
      const arrival = there.dataset.przyjazd;
      const start = form.dataset.termin;
      const departure = back.dataset.odjazd;
      if (departure < arrival || (start && departure < start)) {
        message = "Powrót rusza za wcześnie. Wybierz późniejszy powrót lub wcześniejszy dojazd.";
      } else {
        message = `Łączny koszt: ${money.format(Number(there.dataset.koszt) + Number(back.dataset.koszt))}`;
        const exception = changed || [there, back].some(x => x.dataset.rekomendowany === "false");
        valid = (!changed || consent.checked) && (!exception || reason.value.trim().length > 0);
        if (changed && !consent.checked) message += " · Potwierdź zmianę transportu.";
        else if (exception && !reason.value.trim()) message += " · Podaj uzasadnienie odstępstwa.";
      }
    }
    summary.textContent = message;
    confirm.disabled = !valid;
  };
  form.addEventListener("change", refresh);
  reason.addEventListener("input", refresh);
  form.addEventListener("submit", event => {
    if (event.submitter?.value === "odrzuc" && !reason.value.trim()) {
      event.preventDefault();
      reason.setCustomValidity("Podaj przyczynę przekazania podróży do uzgodnienia.");
      reason.reportValidity();
      reason.focus();
    }
  });
  reason.addEventListener("input", () => reason.setCustomValidity(""));
  refresh();
})();
