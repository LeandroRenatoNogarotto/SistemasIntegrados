CXX ?= g++
CXXFLAGS ?= -O3 -std=c++20 -Wall -Wextra -pedantic
OPENBLAS_FLAGS := $(shell pkg-config --cflags --libs openblas)

BUILD_DIR := ../build
TARGET := $(BUILD_DIR)/cgnr_cpp
BASIC_TARGET := $(BUILD_DIR)/basic_ops_cpp
SERVER_TARGET := $(BUILD_DIR)/cgnr_cpp_server

.PHONY: all clean

all: $(TARGET) $(BASIC_TARGET) $(SERVER_TARGET)

$(TARGET): cgnr.cpp | $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) $< -o $@ $(OPENBLAS_FLAGS)

$(BASIC_TARGET): basic_ops.cpp | $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) $< -o $@ $(OPENBLAS_FLAGS)

$(SERVER_TARGET): cgnr_server.cpp | $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) $< -o $@ $(OPENBLAS_FLAGS) -pthread

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

clean:
	rm -rf $(BUILD_DIR)
