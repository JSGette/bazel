genrule(name="one", srcs=["input"], outs=["one.txt"], cmd="cat $(SRCS) > $@; printf '%s\\n' 'base-rule:06' >> $@", visibility=["//visibility:public"])
genrule(name="two", srcs=[":one"], outs=["two.txt"], cmd="cat $(SRCS) > $@", visibility=["//visibility:public"])
genrule(name="three", srcs=[":two"], outs=["three.txt"], cmd="cat $(SRCS) > $@", visibility=["//visibility:public"])
