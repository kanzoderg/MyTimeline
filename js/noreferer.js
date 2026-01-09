document.addEventListener('DOMContentLoaded', function() {
    const links = document.querySelectorAll('a');
    
    links.forEach(function(link) {
        const currentRel = link.getAttribute('rel');
        
        if (currentRel) {
            // 如果已有 rel 属性，追加 noopener noreferrer
            const relValues = currentRel.split(' ');
            if (!relValues.includes('noopener')) {
                relValues.push('noopener');
            }
            if (!relValues.includes('noreferrer')) {
                relValues.push('noreferrer');
            }
            link.setAttribute('rel', relValues.join(' '));
        } else {
            // 如果没有 rel 属性，直接设置
            link.setAttribute('rel', 'noopener noreferrer');
        }
    });
});
