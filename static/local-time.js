// Время хранится в UTC; показываем его в часовом поясе пользователя
document.querySelectorAll("time[data-local]").forEach((node) => {
    const date = new Date(node.getAttribute("datetime"));
    if (isNaN(date)) return;
    node.textContent = date.toLocaleString("ru-RU", {
        day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
    });
});
