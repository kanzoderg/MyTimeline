document.addEventListener('DOMContentLoaded', function () {
    const links = document.querySelectorAll('a');

    links.forEach(function (link) {
        link.setAttribute('rel', 'noopener noreferrer');
    });
});
