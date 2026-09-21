CXX ?= c++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Wpedantic
CURL_CFLAGS := $(shell curl-config --cflags 2>/dev/null)
CURL_LIBS := $(shell curl-config --libs 2>/dev/null)
ifeq ($(shell uname -s),Darwin)
CRYPTO_LIBS := -framework Security
else
CRYPTO_LIBS := -lcrypto
endif

TARGET := build/scgp_bot
SOURCE := src/main.cpp

.PHONY: build clean

build: $(TARGET)

$(TARGET): $(SOURCE)
	mkdir -p build
	$(CXX) $(CXXFLAGS) $(CURL_CFLAGS) -I src $(SOURCE) -o $@ $(CURL_LIBS) $(CRYPTO_LIBS) -pthread

clean:
	rm -rf build
