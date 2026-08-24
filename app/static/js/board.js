/* Arrastar cartoes entre colunas.
 *
 * Persistimos apenas mudanca de COLUNA. Reordenar dentro da mesma coluna
 * nao e salvo de proposito: nao existe campo de posicao, e cria-lo faria
 * cada arrastada reescrever todos os arquivos daquela coluna -- que e
 * justamente o conflito de merge que a granularidade por arquivo evita.
 */

(function () {
  const listas = document.querySelectorAll("[data-coluna]");
  if (!listas.length) return;

  let arrastou = false;

  // O aviso de coluna vazia e um elemento da lista: se ficar durante o
  // arraste, o Sortable posiciona o cartao depois dele.
  function limparVazios() {
    document.querySelectorAll(".vazio").forEach((p) => p.remove());
  }

  function atualizarContadores() {
    document.querySelectorAll(".coluna").forEach((coluna) => {
      const lista = coluna.querySelector("[data-coluna]");
      const qtd = lista.querySelectorAll(".cartao").length;
      const limite = parseInt(lista.dataset.wip || "0", 10);

      coluna.querySelector("[data-contagem]").textContent = qtd;
      coluna
        .querySelector(".coluna__topo")
        .classList.toggle("coluna__topo--estourado", limite > 0 && qtd > limite);

      // O aviso de coluna vazia some quando chega cartao e volta quando esvazia.
      const vazio = lista.querySelector(".vazio");
      if (qtd > 0 && vazio) vazio.remove();
      if (qtd === 0 && !vazio) {
        const p = document.createElement("p");
        p.className = "vazio";
        p.textContent = "Nada aqui.";
        lista.appendChild(p);
      }
    });
  }

  function avisar(texto) {
    const faixa = document.querySelector("[data-aviso]");
    faixa.textContent = texto;
    faixa.hidden = false;
    setTimeout(() => (faixa.hidden = true), 5000);
  }

  async function mover(cartao, destino, origem, indiceAntigo) {
    try {
      const resposta = await fetch(
        `/api/tarefa/${cartao.dataset.tarefa}/mover`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: destino.dataset.coluna }),
        }
      );

      if (!resposta.ok) {
        const erro = await resposta.json().catch(() => ({}));
        throw new Error(erro.detalhe || `HTTP ${resposta.status}`);
      }
    } catch (e) {
      // Falhou a gravacao: devolve o cartao ao lugar de origem, senao a
      // tela passa a mentir sobre o que esta em disco.
      origem.insertBefore(cartao, origem.children[indiceAntigo] || null);
      atualizarContadores();
      avisar(`Nao foi possivel mover ${cartao.dataset.tarefa}: ${e.message}`);
    }
  }

  listas.forEach((lista) => {
    new Sortable(lista, {
      group: "board",
      draggable: ".cartao",
      animation: 140,
      forceFallback: true, // evita o arraste nativo do navegador nos links
      ghostClass: "cartao--fantasma",
      chosenClass: "cartao--pego",
      onStart: () => {arrsatou = true; limparVazios();},
      onEnd: (evt) => {
        setTimeout(() => (arrastou = false), 60);
        atualizarContadores();
        if (evt.from === evt.to) return; // reordenar na mesma coluna nao persiste
        mover(evt.item, evt.to, evt.from, evt.oldIndex);
      },
    });
  });

  // Sem isso, soltar o cartao dispararia o link de edicao.
  document.querySelectorAll(".cartao").forEach((cartao) => {
    cartao.addEventListener("click", (e) => {
      if (arrastou) e.preventDefault();
    });
  });
})();