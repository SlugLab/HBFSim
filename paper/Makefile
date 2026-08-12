# HBFSim FAST submission
#
#   make          build main.pdf
#   make clean    remove build artifacts (keeps main.pdf)
#   make distclean remove build artifacts and main.pdf
#   make todo     list every remaining draft marker

MAIN = main
TEX  = $(wildcard *.tex)
BIB  = refs.bib

.PHONY: all clean distclean todo

all: $(MAIN).pdf

$(MAIN).pdf: $(TEX) $(BIB) usenix.sty
	pdflatex -interaction=nonstopmode -halt-on-error $(MAIN).tex
	-bibtex $(MAIN)
	pdflatex -interaction=nonstopmode -halt-on-error $(MAIN).tex
	pdflatex -interaction=nonstopmode -halt-on-error $(MAIN).tex

clean:
	rm -f $(MAIN).aux $(MAIN).log $(MAIN).out $(MAIN).bbl $(MAIN).blg \
	      $(MAIN).brf $(MAIN).toc $(MAIN).fdb_latexmk $(MAIN).fls

distclean: clean
	rm -f $(MAIN).pdf

todo:
	@grep -n 'TODO\|NOTE' *.tex || echo "no draft markers left"
